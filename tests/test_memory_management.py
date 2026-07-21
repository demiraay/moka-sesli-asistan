"""Bellek yonetimi: LRU tahliyesi ve kullanici basina kilit.

Tahliye GUVENLI olmalidir — dusen her sey veritabanindan geri kurulabilir.
"""

import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from conftest import stub_llm_script
from core import orchestrator as orchestrator_module
from core.admin_store import AdminStore
from core.orchestrator import AGENT_LOOP_ENABLED, AgentOrchestrator


class MemoryTestCase(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = AdminStore(db_path=str(Path(self.temp_dir.name) / "mem.sqlite3"))
        self.orch = AgentOrchestrator()
        self.orch.admin_store = self.store
        for container in (self.orch.conversation_histories, self.orch.planner_transcripts,
                          self.orch.user_profiles, self.orch.active_sessions,
                          self.orch.call_contexts):
            container.clear()
        self.orch._user_seen.clear()

    def _script(self, tool="answer_general", args=None):
        stub_llm_script(self.orch.llm_client, [
            json.dumps({"tool": tool, "args": args or {"category": "other"}}), "ok"])

    def _turn(self, user_id, text="merhaba", channel="demo"):
        self._script()
        return self.orch.process_turn(text, user_id=user_id, channel=channel)


class TestEviction(MemoryTestCase):
    def test_state_is_bounded_by_max_active_users(self):
        original = orchestrator_module.MAX_ACTIVE_USERS
        orchestrator_module.MAX_ACTIVE_USERS = 3
        try:
            for index in range(10):
                self._turn(f"u{index}")
            self.assertLessEqual(len(self.orch._user_seen), 3)
            self.assertLessEqual(len(self.orch.user_profiles), 3)
            self.assertLessEqual(len(self.orch.conversation_histories), 3)
            self.assertLessEqual(len(self.orch.planner_transcripts), 3)
        finally:
            orchestrator_module.MAX_ACTIVE_USERS = original

    def test_oldest_user_is_dropped_first(self):
        original = orchestrator_module.MAX_ACTIVE_USERS
        orchestrator_module.MAX_ACTIVE_USERS = 2
        try:
            self._turn("eski")
            self._turn("orta")
            self._turn("yeni")
            self.assertNotIn("eski", self.orch.user_profiles)
            self.assertIn("yeni", self.orch.user_profiles)
        finally:
            orchestrator_module.MAX_ACTIVE_USERS = original

    def test_recent_activity_protects_a_user(self):
        """LRU: konusmaya devam eden kullanici dusmemeli."""
        original = orchestrator_module.MAX_ACTIVE_USERS
        orchestrator_module.MAX_ACTIVE_USERS = 2
        try:
            self._turn("sadik")
            self._turn("gecici1")
            self._turn("sadik")          # tekrar dokun
            self._turn("gecici2")
            self.assertIn("sadik", self.orch.user_profiles)
        finally:
            orchestrator_module.MAX_ACTIVE_USERS = original

    def test_forget_user_clears_every_container(self):
        self._turn("silinecek")
        self.orch.set_call_context("silinecek", "M-1001")
        self.orch._forget_user("silinecek")

        self.assertNotIn("silinecek", self.orch.user_profiles)
        self.assertNotIn("silinecek", self.orch.call_contexts)
        self.assertNotIn("demo:silinecek", self.orch.conversation_histories)
        self.assertNotIn("demo:silinecek", self.orch.planner_transcripts)
        self.assertNotIn("demo:silinecek", self.orch.active_sessions)

    def test_forgetting_one_user_does_not_touch_another(self):
        self._turn("kalan")
        self._turn("giden")
        self.orch._forget_user("giden")
        self.assertIn("kalan", self.orch.user_profiles)
        self.assertIn("demo:kalan", self.orch.conversation_histories)

    def test_similar_user_ids_are_not_confused(self):
        """'u1' dusurulurken 'xu1' etkilenmemeli (suffix eslesmesi tuzagi)."""
        self._turn("u1")
        self._turn("xu1")
        self.orch._forget_user("u1")
        self.assertIn("demo:xu1", self.orch.conversation_histories)
        self.assertNotIn("demo:u1", self.orch.conversation_histories)

    def test_zero_means_no_eviction(self):
        original = orchestrator_module.MAX_ACTIVE_USERS
        orchestrator_module.MAX_ACTIVE_USERS = 0
        try:
            for index in range(6):
                self._turn(f"sinirsiz{index}")
            self.assertEqual(len(self.orch.user_profiles), 6)
        finally:
            orchestrator_module.MAX_ACTIVE_USERS = original


class TestEvictionIsLossless(MemoryTestCase):
    """Tahliye VERI KAYBI DEGILDIR: her sey DB'den geri gelir."""

    def test_evicted_user_recovers_history_and_profile(self):
        self.orch.set_call_context("donen", "M-1001")
        self._script("get_settlement_status", {"period": "latest"})
        self.orch.process_turn("Param ne zaman yatacak?", user_id="donen", channel="demo")

        session_before = self.orch._get_session_id("donen", "demo")
        self.orch._forget_user("donen")
        self.assertNotIn("donen", self.orch.user_profiles)

        # Geri donus: gecmis ve oturum DB'den yeniden kurulmali
        history = self.orch._get_conversation_history("donen", "demo")
        self.assertTrue(history, "gecmis DB'den geri yuklenmedi")
        self.assertEqual(self.orch._get_session_id("donen", "demo"), session_before)

        profile = self.orch._get_user_profile("donen")
        self.assertEqual(profile.get("merchant_id"), "M-1001")

    @unittest.skipIf(not AGENT_LOOP_ENABLED,
                     "planlayıcı transkripti yalnızca agent loop'ta var")
    def test_evicted_user_recovers_the_planner_transcript(self):
        self.orch.set_call_context("donen2", "M-1001")
        self._script("get_settlement_status", {"period": "latest"})
        self.orch.process_turn("Param ne zaman yatacak?", user_id="donen2", channel="demo")

        self.orch._forget_user("donen2")
        rebuilt = self.orch._get_planner_transcript("donen2", "demo")
        self.assertTrue(any(m["role"] == "tool" and m.get("content") for m in rebuilt),
                        "arac sonuclari DB'den geri gelmedi")


class TestConcurrency(MemoryTestCase):
    def test_same_user_turns_are_serialised(self):
        """Ayni kullanicidan es zamanli iki mesaj birbirini bozmamali."""
        overlaps = []
        inside = threading.Event()

        real = self.orch._process_turn_locked

        def slow(*args, **kwargs):
            if inside.is_set():
                overlaps.append(args)        # baska bir tur zaten icerideydi
            inside.set()
            try:
                return real(*args, **kwargs)
            finally:
                inside.clear()

        self.orch._process_turn_locked = slow
        stub_llm_script(self.orch.llm_client, [
            '{"tool": "answer_general", "args": {"category": "other"}}', "ok"])

        threads = [threading.Thread(target=self.orch.process_turn,
                                    args=("merhaba",), kwargs={"user_id": "esz", "channel": "demo"})
                   for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)

        self.assertEqual(overlaps, [], "ayni kullanicinin turlari ic ice girdi")
        # Testin YANLIS sebeple gecmedigini dogrula: turlar gercekten kostu mu?
        self.assertTrue(self.orch.conversation_histories.get("demo:esz"),
                        "hicbir tur calismamis — test bos yere geciyor")

    def test_different_users_get_different_locks(self):
        self.assertIsNot(self.orch._lock_for_user("a"), self.orch._lock_for_user("b"))
        self.assertIs(self.orch._lock_for_user("a"), self.orch._lock_for_user("a"))

    def test_locks_do_not_leak_after_eviction(self):
        original = orchestrator_module.MAX_ACTIVE_USERS
        orchestrator_module.MAX_ACTIVE_USERS = 2
        try:
            for index in range(8):
                self._turn(f"kilit{index}")
            self.assertLessEqual(len(self.orch._user_locks), 3)
        finally:
            orchestrator_module.MAX_ACTIVE_USERS = original


if __name__ == "__main__":
    unittest.main()
