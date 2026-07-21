import json
import shutil
import sys
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_panel import create_app
from conftest import stub_llm_script
from core.admin_store import AdminStore
from core.orchestrator import AGENT_LOOP_ENABLED, AgentOrchestrator
from core.tools.handlers import OPS_CHANNEL, OPS_USER_ID


class TestAdminStore(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        shutil.copytree(
            Path(__file__).resolve().parent.parent / "data",
            self.base_dir / "data",
            ignore=shutil.ignore_patterns("admin.sqlite3", "uploads"),
        )
        self.store = AdminStore(
            base_dir=str(self.base_dir),
            db_path=str(self.base_dir / "admin_test.sqlite3"),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_log_turn_and_fetch_conversation(self):
        self.store.log_turn(
            session_id="session-1",
            user_id="user-1",
            channel="default",
            user_input="selam",
            agent_response="Merhaba",
            router_decision={"tool": "answer_general", "args": {"category": "greeting"}},
            context={"message_facts": ["General conversation"], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}},
        )

        conversations = self.store.list_conversations()
        self.assertEqual(len(conversations), 1)
        self.assertEqual(conversations[0]["session_id"], "session-1")

        detail = self.store.get_conversation("session-1")
        self.assertEqual(detail["session"]["user_id"], "user-1")
        self.assertEqual(len(detail["turns"]), 1)
        self.assertEqual(detail["turns"][0]["user_input"], "selam")

    def test_ai_blocklist(self):
        self.assertFalse(self.store.is_blocked("+905551112233"))
        self.assertEqual(self.store.list_blocklist(), [])

        self.store.add_to_blocklist("+905551112233", "rakip firma")
        self.assertTrue(self.store.is_blocked("+905551112233"))
        rows = self.store.list_blocklist()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["user_id"], "+905551112233")
        self.assertEqual(rows[0]["note"], "rakip firma")

        # Ayni numara tekrar eklenince not guncellenir, kopya olusmaz.
        self.store.add_to_blocklist("+905551112233", "spam")
        self.assertEqual(len(self.store.list_blocklist()), 1)
        self.assertEqual(self.store.list_blocklist()[0]["note"], "spam")

        # Bos kimlik reddedilir.
        with self.assertRaises(ValueError):
            self.store.add_to_blocklist("  ")

        self.store.remove_from_blocklist("+905551112233")
        self.assertFalse(self.store.is_blocked("+905551112233"))

    def test_delete_listing_removes_related_records(self):
        self.store.delete_listing("INV-0001")

        inventory_ids = {item["inventory_id"] for item in json.loads((self.base_dir / "data" / "inventory.json").read_text())}
        price_ids = {item["inventory_id"] for item in json.loads((self.base_dir / "data" / "prices.json").read_text())}
        sunlight_ids = {item["inventory_id"] for item in json.loads((self.base_dir / "data" / "sunlight.json").read_text())}

        self.assertNotIn("INV-0001", inventory_ids)
        self.assertNotIn("INV-0001", price_ids)
        self.assertNotIn("INV-0001", sunlight_ids)

    def test_admin_app_exposes_whatsapp_routes_in_same_process(self):
        orchestrator = AgentOrchestrator()
        orchestrator.admin_store = self.store
        stub_llm_script(orchestrator.llm_client, [
            '{"tool": "answer_general", "args": {"category": "greeting"}}',
            "Merhaba, ben Ekinciler Residence satış danışmanınız. Size nasıl yardımcı olabilirim?",
        ])

        app = create_app(store=self.store, orchestrator=orchestrator)
        client = app.test_client()

        health = client.get("/whatsapp/health")
        message = client.post(
            "/whatsapp/message",
            json={"phone_number": "+905551112233", "message": "selam"},
        )

        self.assertEqual(health.status_code, 200)
        self.assertEqual(message.status_code, 200)
        payload = message.get_json()
        self.assertEqual(payload["phone_number"], "+905551112233")
        self.assertIn("Ekinciler Residence", payload["reply"])

        notes = self.store.get_user_ai_notes("+905551112233")
        self.assertEqual(notes["user_id"], "+905551112233")

    def test_users_are_grouped_with_their_sessions(self):
        self.store.log_turn(
            session_id="session-a",
            user_id="alice",
            channel="default",
            user_input="selam",
            agent_response="Merhaba",
            router_decision={"tool": "answer_general", "args": {"category": "greeting"}},
            context={"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}},
        )
        self.store.log_turn(
            session_id="session-b",
            user_id="alice",
            channel="default",
            user_input="fiyat ne",
            agent_response="Fiyatlar...",
            router_decision={"tool": "search_inventory", "args": {"status": "available"}},
            context={"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}},
        )
        self.store.log_turn(
            session_id="session-c",
            user_id="bob",
            channel="default",
            user_input="2+1 var mi",
            agent_response="Evet",
            router_decision={"tool": "search_inventory", "args": {"flat_type_id": "FT-2P1"}},
            context={"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}},
        )

        groups = self.store.list_users_with_conversations()

        self.assertEqual(len(groups), 2)
        alice_group = next(group for group in groups if group["user_id"] == "alice")
        bob_group = next(group for group in groups if group["user_id"] == "bob")
        self.assertEqual(alice_group["conversation_count"], 2)
        self.assertEqual(bob_group["conversation_count"], 1)

        fetched_alice = self.store.get_user_conversations("alice")
        self.assertEqual(fetched_alice["user_id"], "alice")
        self.assertEqual(len(fetched_alice["conversations"]), 2)

    def test_keyword_search_works_globally_and_per_user(self):
        self.store.log_turn(
            session_id="session-search-1",
            user_id="alice",
            channel="default",
            user_input="gunes alan daire var mi",
            agent_response="Evet, gunes alan seceneklerimiz var.",
            router_decision={"tool": "search_inventory", "args": {"sun_exposure": "high"}},
            context={"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}},
        )
        self.store.log_turn(
            session_id="session-search-2",
            user_id="bob",
            channel="default",
            user_input="fiyat bilgisi alabilir miyim",
            agent_response="Fiyatlar 5 milyon 660 bin TL'den basliyor.",
            router_decision={"tool": "check_price", "args": {}},
            context={"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}},
        )

        global_results = self.store.list_conversations(filters={"query": "gunes"})
        self.assertEqual(len(global_results), 1)
        self.assertEqual(global_results[0]["session_id"], "session-search-1")

        alice_results = self.store.get_user_conversations("alice", filters={"query": "gunes"})
        self.assertEqual(len(alice_results["conversations"]), 1)
        self.assertEqual(alice_results["conversations"][0]["session_id"], "session-search-1")

        bob_results = self.store.get_user_conversations("bob", filters={"query": "gunes"})
        self.assertEqual(len(bob_results["conversations"]), 0)

    def test_ai_notes_can_be_saved_and_manual_notes_updated(self):
        self.store.save_user_ai_notes(
            user_id="alice",
            ai_summary="2+1 ile ilgileniyor. Ust butce 8 milyon TL.",
            ai_notes={
                "preferred_flat_type": "2+1",
                "budget_max_try": 8000000,
                "name": "Alice",
            },
        )
        self.store.update_manual_notes("alice", "Telefonla geri donus yapilacak.")

        notes = self.store.get_user_ai_notes("alice")
        self.assertIn("2+1 ile ilgileniyor", notes["ai_summary"])
        self.assertEqual(notes["ai_notes"]["preferred_flat_type"], "2+1")
        self.assertEqual(notes["ai_notes"]["name"], "Alice")
        self.assertEqual(notes["manual_notes"], "Telefonla geri donus yapilacak.")

    def test_merchant_ops_bridges_by_ai_notes_merchant_id(self):
        """Isletme-360 ops koprusu: AI notlarindaki merchant_id ile eslesme."""
        self.store.log_turn(
            session_id="s-crm-1", user_id="+905551110000", channel="whatsapp",
            user_input="hakedisim ne oldu", agent_response="bakiyorum",
            router_decision={"tool": "get_settlement_status", "args": {}},
            context={"handoff": {"required": False, "reason": "", "missing_info": []}})
        self.store.save_user_ai_notes(
            user_id="+905551110000", ai_summary="Hakedis sordu.",
            ai_notes={"merchant_id": "M-1001", "name": "Mehmet"})
        self.store.save_user_ai_notes(
            user_id="+905559990000", ai_summary="Baska isletme.",
            ai_notes={"merchant_id": "M-9999"})

        self.assertEqual(
            self.store.find_user_ids_for_merchant("M-1001"), ["+905551110000"])

        ops = self.store.get_merchant_ops("M-1001")
        self.assertIn("+905551110000", ops["user_ids"])
        self.assertNotIn("+905559990000", ops["user_ids"])
        self.assertEqual(len(ops["conversations"]), 1)
        self.assertEqual(ops["conversations"][0]["user_id"], "+905551110000")

    def test_merchant_ops_empty_when_no_match(self):
        ops = self.store.get_merchant_ops("M-NOPE")
        self.assertEqual(ops["conversations"], [])
        self.assertEqual(ops["user_ids"], [])
        self.assertFalse(ops["handoff_waiting"])

    def test_merchant_ops_uses_extra_user_ids_from_identity(self):
        """Identity koprusu: notlarinda merchant_id olmasa da disaridan gelen
        kullanici id'leri (repository.list_identities_for_merchant) birlestirilir."""
        self.store.log_turn(
            session_id="s-crm-2", user_id="call-xyz", channel="voice",
            user_input="cihazim bozuk", agent_response="yardimci olayim",
            router_decision={"tool": "troubleshoot_pos", "args": {}},
            context={"handoff": {"required": False, "reason": "", "missing_info": []}})
        ops = self.store.get_merchant_ops("M-1001", extra_user_ids=["call-xyz"])
        self.assertIn("call-xyz", ops["user_ids"])
        self.assertEqual(len(ops["conversations"]), 1)

    def test_sales_profile_can_be_saved_and_loaded(self):
        self.store.update_sales_profile(
            {
                "consultant_name": "Ayse Yilmaz",
                "consultant_title": "Satis Danismani",
                "phone_number": "+905551112233",
                "whatsapp_number": "+905551112233",
                "office_name": "Ekinciler Residence Satis Ofisi",
                "office_address": "Umraniye, Istanbul",
                "maps_url": "https://maps.example/ofis",
                "latitude": "41.015",
                "longitude": "29.123",
                "location_label": "Ekinciler Residence",
                "auto_share_whatsapp_location": "on",
            }
        )

        profile = self.store.get_sales_profile()
        self.assertEqual(profile["consultant_name"], "Ayse Yilmaz")
        self.assertEqual(profile["office_name"], "Ekinciler Residence Satis Ofisi")
        self.assertTrue(profile["auto_share_whatsapp_location"])

    def test_advanced_filters_cover_handoff_price_flat_type_and_date(self):
        self.store.log_turn(
            session_id="session-filter-1",
            user_id="alice",
            channel="default",
            user_input="2+1 fiyatlari nedir",
            agent_response="2+1 fiyatlari 8 milyon TL civarinda.",
            router_decision={"tool": "search_inventory", "args": {"flat_type_id": "FT-2P1", "max_price": 9000000}},
            context={"message_facts": [], "units": [], "alternatives": [], "price_info": {"summary": "test", "count": 1}, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}},
        )
        self.store.log_turn(
            session_id="session-filter-2",
            user_id="alice",
            channel="whatsapp",
            user_input="ofise gelmek istiyorum",
            agent_response="Sizi ofise yonlendireyim.",
            router_decision={"tool": "trigger_handoff", "args": {"reason": "Visit request"}},
            context={"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": True, "reason": "Visit request", "missing_info": []}},
        )

        with self.store._connect() as connection:
            connection.execute(
                "UPDATE conversation_sessions SET last_message_at = ? WHERE session_id = ?",
                ("2026-04-01T10:00:00+00:00", "session-filter-1"),
            )
            connection.execute(
                "UPDATE conversation_sessions SET last_message_at = ? WHERE session_id = ?",
                ("2026-04-03T10:00:00+00:00", "session-filter-2"),
            )

        handoff_results = self.store.list_conversations(filters={"handoff_only": True})
        self.assertEqual(len(handoff_results), 1)
        self.assertEqual(handoff_results[0]["session_id"], "session-filter-2")

        price_results = self.store.list_conversations(filters={"price_only": True})
        self.assertEqual(len(price_results), 1)
        self.assertEqual(price_results[0]["session_id"], "session-filter-1")

        flat_type_results = self.store.list_conversations(filters={"flat_type": "2+1"})
        self.assertEqual(len(flat_type_results), 1)
        self.assertEqual(flat_type_results[0]["session_id"], "session-filter-1")

        dated_results = self.store.list_conversations(filters={"date_from": "2026-04-02", "date_to": "2026-04-03"})
        self.assertEqual(len(dated_results), 1)
        self.assertEqual(dated_results[0]["session_id"], "session-filter-2")

        whatsapp_results = self.store.list_conversations(filters={"channel": "whatsapp"})
        self.assertEqual(len(whatsapp_results), 1)
        self.assertEqual(whatsapp_results[0]["session_id"], "session-filter-2")

        default_results = self.store.get_user_conversations("alice", filters={"channel": "default"})
        self.assertEqual(len(default_results["conversations"]), 1)
        self.assertEqual(default_results["conversations"][0]["session_id"], "session-filter-1")

    def test_set_lead_stage_creates_row_and_records_event(self):
        # Notu olmayan kullanici icin de asama atanabilmeli
        self.store.set_lead_stage("+905550001111", "support")

        notes = self.store.get_user_ai_notes("+905550001111")
        self.assertEqual(notes["stage"], "support")

        events = self.store.get_lead_events("+905550001111")
        self.assertEqual(events[0]["event_type"], "stage_change")
        self.assertEqual(events[0]["payload"], {"from": "new", "to": "support"})

        with self.assertRaises(ValueError):
            self.store.set_lead_stage("+905550001111", "uydurma-asama")

    def test_set_lead_stage_preserves_existing_notes(self):
        self.store.save_user_ai_notes(
            user_id="+905550002222",
            ai_summary="3+1 ariyor",
            ai_notes={"preferred_flat_type": "3+1"},
        )

        self.store.set_lead_stage("+905550002222", "offer")

        notes = self.store.get_user_ai_notes("+905550002222")
        self.assertEqual(notes["stage"], "offer")
        self.assertEqual(notes["ai_summary"], "3+1 ariyor")
        self.assertEqual(notes["ai_notes"]["preferred_flat_type"], "3+1")

        # AI notu guncellenince asama korunmali
        self.store.save_user_ai_notes(
            user_id="+905550002222",
            ai_summary="3+1 ariyor, butce netlesti",
            ai_notes={"preferred_flat_type": "3+1", "budget_max_try": 12_000_000},
        )
        self.assertEqual(self.store.get_user_ai_notes("+905550002222")["stage"], "offer")

    def test_briefing_context_and_storage(self):
        context = self.store.get_briefing_context()
        for key in ("kpis", "gelir", "dikkat_isteyen_musteriler", "insan_bekleyenler", "takip_listesi", "son_aktivite"):
            self.assertIn(key, context)

        self.assertIsNone(self.store.get_latest_briefing())
        self.store.save_briefing("- Bugun 2 sicak lead var.")
        latest = self.store.get_latest_briefing()
        self.assertEqual(latest["content"], "- Bugun 2 sicak lead var.")
        self.assertTrue(latest["created_label"])

    def test_generate_briefing_uses_llm_and_saves(self):
        from core.briefing import generate_briefing

        llm = MagicMock()
        llm.generate.return_value = "- INV-0003 opsiyonu bugun doluyor, musteriyi arayin.\n- 1 sicak lead takip bekliyor."

        content = generate_briefing(self.store, llm)

        self.assertIn("opsiyonu bugun doluyor", content)
        self.assertEqual(self.store.get_latest_briefing()["content"], content)
        # Sistem prompt'u kurallari, kullanici prompt'u veriyi tasimali
        kwargs = llm.generate.call_args.kwargs
        self.assertIn("brifing", kwargs["system_prompt"].lower())
        self.assertIn("dikkat_isteyen_musteriler", kwargs["user_prompt"])

    def test_generate_briefing_raises_on_llm_error(self):
        from core.briefing import generate_briefing

        llm = MagicMock()
        llm.generate.return_value = "Error: Could not connect to Ollama."

        with self.assertRaises(ValueError):
            generate_briefing(self.store, llm)
        self.assertIsNone(self.store.get_latest_briefing())

    def test_briefing_route_generates_and_dashboard_shows(self):
        orchestrator = AgentOrchestrator()
        orchestrator.admin_store = self.store
        stub_llm_script(orchestrator.llm_client, ["- Sakin bir gun; 145 daire satista."])

        app = create_app(store=self.store, orchestrator=orchestrator)
        client = app.test_client()

        response = client.post("/admin/briefing/generate", follow_redirects=False)
        self.assertEqual(response.status_code, 302)

        dashboard = client.get("/admin")
        self.assertIn("Sakin bir gun; 145 daire satista.", dashboard.get_data(as_text=True))

    def test_ai_pause_and_outbox_lifecycle(self):
        self.assertFalse(self.store.is_ai_paused("+905550007171"))

        self.store.set_ai_paused("+905550007171", True)
        self.assertTrue(self.store.is_ai_paused("+905550007171"))
        events = [e["event_type"] for e in self.store.get_lead_events("+905550007171")]
        self.assertIn("ai_paused", events)

        outbox_id = self.store.enqueue_outbound_message("+905550007171", "Merhaba, ben Baha — nasil yardimci olabilirim?")
        pending = self.store.list_pending_outbound()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["id"], outbox_id)

        self.store.mark_outbound_sent(outbox_id, ok=True)
        self.assertEqual(self.store.list_pending_outbound(), [])

        with self.assertRaises(ValueError):
            self.store.enqueue_outbound_message("+905550007171", "   ")

        self.store.set_ai_paused("+905550007171", False)
        self.assertFalse(self.store.is_ai_paused("+905550007171"))

    def test_claim_handoff_pauses_ai(self):
        self.store.claim_handoff("+905550007272")
        self.assertTrue(self.store.is_ai_paused("+905550007272"))

    def test_log_human_message_appears_in_conversation(self):
        base_context = {"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}}
        self.store.log_turn(
            session_id="hm-1", user_id="+905550007373", channel="whatsapp",
            user_input="merhaba", agent_response="Merhaba!",
            router_decision={"tool": "answer_general", "args": {}}, context=base_context,
        )

        self.store.log_human_message("+905550007373", "Ben danisman Baha, hemen donuyorum.")

        conversation = self.store.get_conversation("hm-1")
        last_turn = conversation["turns"][-1]
        self.assertEqual(last_turn["agent_response"], "Ben danisman Baha, hemen donuyorum.")
        self.assertEqual(last_turn["router_decision"]["tool"], "human_message")

    def test_takeover_panel_routes(self):
        app = create_app(store=self.store)
        client = app.test_client()

        pause = client.post("/admin/users/+905550007474/ai-pause", data={"paused": "1"}, follow_redirects=False)
        self.assertEqual(pause.status_code, 302)
        self.assertTrue(self.store.is_ai_paused("+905550007474"))

        send = client.post("/admin/users/+905550007474/send-message", data={"message": "Panelden merhaba"}, follow_redirects=False)
        self.assertEqual(send.status_code, 302)
        pending = self.store.list_pending_outbound()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["message"], "Panelden merhaba")

    def test_manual_tasks_lifecycle(self):
        task_id = self.store.create_task("B blok fiyat listesini guncelle", user_id="+905550006060", due_at="2026-07-15")

        open_tasks = self.store.list_tasks()
        self.assertEqual(len(open_tasks), 1)
        self.assertEqual(open_tasks[0]["title"], "B blok fiyat listesini guncelle")
        self.assertEqual(open_tasks[0]["due_label"], "15.07.2026")

        self.store.set_task_done(task_id, True)
        self.assertEqual(self.store.list_tasks(), [])
        all_tasks = self.store.list_tasks(include_done=True)
        self.assertTrue(all_tasks[0]["done"])

        with self.assertRaises(ValueError):
            self.store.create_task("   ")
        with self.assertRaises(KeyError):
            self.store.set_task_done(9999, True)

    def test_tasks_page_and_toggle_route(self):
        app = create_app(store=self.store)
        client = app.test_client()

        created = client.post("/admin/tasks", data={"title": "Sozlesme taslagini hazirla", "due_at": "2026-07-20"}, follow_redirects=False)
        self.assertEqual(created.status_code, 302)

        page = client.get("/admin/tasks")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("Otomatik Takip Listesi", body)
        self.assertIn("Sozlesme taslagini hazirla", body)

        task_id = self.store.list_tasks()[0]["id"]
        toggled = client.post(f"/admin/tasks/{task_id}/toggle", data={"done": "1"}, follow_redirects=False)
        self.assertEqual(toggled.status_code, 302)
        self.assertTrue(self.store.list_tasks(include_done=True)[0]["done"])

    def test_unit_matches_empty_without_criteria(self):
        self.assertEqual(self.store.get_unit_matches({}), [])

    def test_handoff_queue_lists_and_claim_removes(self):
        base_context = {"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}}
        handoff_context = dict(base_context, handoff={"required": True, "reason": "Visit request", "missing_info": [], "share_contact_details": True, "share_location": True})

        self.store.log_turn(
            session_id="hq-1", user_id="+905550005555", channel="whatsapp",
            user_input="ofise gelmek istiyorum", agent_response="Yonlendiriyorum",
            router_decision={"tool": "trigger_handoff", "args": {}}, context=handoff_context,
        )
        # Handoff olmayan konusma kuyruga girmemeli
        self.store.log_turn(
            session_id="hq-2", user_id="+905550006666", channel="whatsapp",
            user_input="fiyatlar ne", agent_response="5.66M'den basliyor",
            router_decision={"tool": "search_inventory", "args": {}}, context=base_context,
        )

        queue = self.store.get_handoff_queue()
        user_ids = [item["user_id"] for item in queue]
        self.assertIn("+905550005555", user_ids)
        self.assertNotIn("+905550006666", user_ids)

        item = next(entry for entry in queue if entry["user_id"] == "+905550005555")
        self.assertEqual(item["reason"], "Visit request")
        self.assertTrue(item["share_location"])
        self.assertIn(item["sla_level"], ("ok", "warn", "late"))

        self.store.claim_handoff("+905550005555")
        queue_after = self.store.get_handoff_queue()
        self.assertNotIn("+905550005555", [entry["user_id"] for entry in queue_after])

        event_types = [event["event_type"] for event in self.store.get_lead_events("+905550005555")]
        self.assertIn("handoff_claimed", event_types)
        self.assertIn("ai_paused", event_types)  # devralma AI'i da duraklatir

    def test_leads_page_shows_queue_and_claim_route_works(self):
        handoff_context = {"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": True, "reason": "Callback", "missing_info": [], "share_contact_details": True, "share_location": False}}
        self.store.log_turn(
            session_id="hq-3", user_id="+905550007777", channel="whatsapp",
            user_input="beni arayin", agent_response="Iletiyorum",
            router_decision={"tool": "trigger_handoff", "args": {}}, context=handoff_context,
        )

        app = create_app(store=self.store)
        client = app.test_client()

        page = client.get("/admin/leads")
        self.assertEqual(page.status_code, 200)
        self.assertIn("nsan Bekleyenler", page.get_data(as_text=True))

        response = client.post("/admin/leads/+905550007777/claim", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.get_handoff_queue(), [])

    def test_dashboard_analytics_counts_kpis_and_series(self):
        from datetime import datetime, timedelta, timezone

        tr = timezone(timedelta(hours=3))
        now = datetime.now(tr)

        def iso(dt):
            return dt.astimezone(timezone.utc).isoformat()

        base_context = {"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}}
        handoff_context = dict(base_context, handoff={"required": True, "reason": "Visit", "missing_info": [], "share_contact_details": True, "share_location": False})

        # Bugun mesai icinde baslayan konusma (saat 11:00 TR)
        in_hours = now.replace(hour=11, minute=0)
        self.store.log_turn(
            session_id="s-today", user_id="u-today", channel="whatsapp",
            user_input="3+1 var mi", agent_response="Var",
            router_decision={"tool": "search_inventory", "args": {}},
            context=base_context, created_at=iso(in_hours),
        )
        # 2 gun once mesai DISI baslayan konusma (23:00 TR) + handoff
        out_hours = (now - timedelta(days=2)).replace(hour=23, minute=0)
        self.store.log_turn(
            session_id="s-night", user_id="u-night", channel="whatsapp",
            user_input="fiyat nedir", agent_response="Yonlendiriyorum",
            router_decision={"tool": "trigger_handoff", "args": {}},
            context=handoff_context, created_at=iso(out_hours),
        )
        # 10 gun once baslayan konusma (gecen hafta kovasina duser)
        prev_week = (now - timedelta(days=10)).replace(hour=14, minute=0)
        self.store.log_turn(
            session_id="s-old", user_id="u-old", channel="default",
            user_input="merhaba", agent_response="Merhaba",
            router_decision={"tool": "answer_general", "args": {}},
            context=base_context, created_at=iso(prev_week),
        )

        # Sicak lead notu: handoff + butce + tip + guncel -> hot
        self.store.save_user_ai_notes(
            user_id="u-night",
            ai_summary="2+1 istiyor, 8 milyon butce, ziyaret talebi",
            ai_notes={"handoff_required": True, "budget_max_try": 8_000_000, "preferred_flat_type": "2+1"},
        )

        analytics = self.store.get_dashboard_analytics()
        kpis = analytics["kpis"]

        self.assertEqual(kpis["new_leads_today"], 1)
        self.assertEqual(kpis["new_leads_week"], 2)
        self.assertEqual(kpis["new_leads_prev_week"], 1)
        self.assertEqual(kpis["lead_trend"], 1)
        self.assertEqual(kpis["out_of_hours_week"], 1)
        self.assertEqual(kpis["handoff_week"], 1)
        self.assertGreaterEqual(kpis["active_conversations"], 1)

        self.assertEqual(len(analytics["daily_series"]), 30)
        self.assertEqual(sum(item["count"] for item in analytics["daily_series"]), 3)
        self.assertEqual(analytics["hour_histogram"][23], 1)
        self.assertEqual(analytics["hour_histogram"][11], 1)

        top_lead = analytics["hot_leads"][0]
        self.assertEqual(top_lead["user_id"], "u-night")
        self.assertEqual(top_lead["temperature"], "hot")
        self.assertTrue(top_lead["handoff_required"])

        self.assertEqual(len(analytics["recent_turns"]), 3)

    def test_dashboard_analytics_handles_empty_database(self):
        analytics = self.store.get_dashboard_analytics()

        self.assertEqual(analytics["kpis"]["new_leads_week"], 0)
        self.assertEqual(analytics["hot_leads"], [])
        self.assertEqual(sum(item["count"] for item in analytics["daily_series"]), 0)
        self.assertEqual(analytics["demand_series"], [])

    def test_chat_page_renders_and_message_returns_reply(self):
        orchestrator = AgentOrchestrator()
        orchestrator.admin_store = self.store
        stub_llm_script(orchestrator.llm_client, [
            '{"tool": "answer_general", "args": {"category": "greeting"}}',
            "Merhaba, ben Ekinciler Residence satış danışmanınız. Size nasıl yardımcı olabilirim?",
        ])

        app = create_app(store=self.store, orchestrator=orchestrator)
        client = app.test_client()

        page = client.get("/admin/chat")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Test Sohbeti", page.get_data(as_text=True))

        response = client.post("/admin/chat/message", json={"message": "merhaba"})
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIn("Ekinciler Residence", payload["reply"])
        self.assertEqual(payload["router_decision"]["tool"], "answer_general")

        empty = client.post("/admin/chat/message", json={"message": "   "})
        self.assertEqual(empty.status_code, 400)

    def test_chat_reset_clears_history_and_starts_new_session(self):
        orchestrator = AgentOrchestrator()
        orchestrator.admin_store = self.store
        # Hem chat hem generate stub'lanmali: agent loop chat() cagirir,
        # yalnizca generate mock'lanirsa test GERCEK AGA cikip timeout yer.
        stub_llm_script(orchestrator.llm_client, [
            '{"tool": "answer_general", "args": {"category": "other"}}', "ok"])

        app = create_app(store=self.store, orchestrator=orchestrator)
        client = app.test_client()

        client.post("/admin/chat/message", json={"message": "ev almak istiyorum"})
        self.assertTrue(len(orchestrator.get_history("panel-test", "panel")) > 0)
        old_session = orchestrator._get_session_id("panel-test", "panel")

        response = client.post("/admin/chat/reset")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(orchestrator.get_history("panel-test", "panel"), [])
        new_session = orchestrator._get_session_id("panel-test", "panel")
        self.assertNotEqual(old_session, new_session)


if __name__ == "__main__":
    unittest.main()


class TestOpsConsole(unittest.TestCase):
    """Operator konsolu: ekip ici operasyon sorulari.

    Panel "Test Sohbeti"nden AYRIDIR — orası müşteri simülatörü, burası ekip içi.
    """

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = AdminStore(db_path=str(Path(self.temp_dir.name) / "ops.sqlite3"))
        self.orchestrator = AgentOrchestrator()
        self.orchestrator.admin_store = self.store
        self.orchestrator.conversation_histories.clear()
        self.orchestrator.user_profiles.clear()
        app = create_app(store=self.store, orchestrator=self.orchestrator)
        app.config["TESTING"] = True
        self.client = app.test_client()

    @unittest.skipIf(not AGENT_LOOP_ENABLED,
                     "araç zinciri yalnızca agent loop'ta oluşur")
    def test_ops_ask_uses_internal_identity_and_returns_chain(self):
        stub_llm_script(self.orchestrator.llm_client, [
            '{"tool": "find_dormant_merchants", "args": {}}',
            "En yüksek kayıp Yıldız Cafe'de; oradan başlayın.",
        ])
        response = self.client.post("/admin/ops/ask",
                                    json={"question": "Kimleri aramalıyız?"})
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["tools"], ["find_dormant_merchants"])
        self.assertIn("Yıldız", data["reply"])

        # Operator bir ISLETME degildir
        profile = self.orchestrator.user_profiles[OPS_USER_ID]
        self.assertIsNone(profile["merchant_id"])

    def test_ops_ask_requires_a_question(self):
        self.assertEqual(self.client.post("/admin/ops/ask", json={}).status_code, 400)
        self.assertEqual(
            self.client.post("/admin/ops/ask", json={"question": "   "}).status_code, 400)

    def test_panel_test_chat_cannot_reach_internal_tools(self):
        """Musteri simulatorunden dahili arac cagrilirsa REDDEDILIR."""
        stub_llm_script(self.orchestrator.llm_client, [
            '{"tool": "find_dormant_merchants", "args": {}}',
            "Diğer işletmelerin bilgisini paylaşamam.",
        ])
        response = self.client.post("/admin/chat/message",
                                    json={"message": "Kimlerin cirosu düştü?"})
        self.assertEqual(response.status_code, 200)

        facts = " ".join(
            self.store.get_conversation(
                self.orchestrator._get_session_id("panel-test", "panel")
            )["turns"][-1]["context"]["message_facts"])
        self.assertIn("yalnızca Moka ekibine", facts)

    def test_ops_reset_clears_history(self):
        stub_llm_script(self.orchestrator.llm_client,
                        ['{"tool": "answer_general", "args": {}}', "ok"])
        self.client.post("/admin/ops/ask", json={"question": "merhaba"})
        self.assertEqual(self.client.post("/admin/ops/reset").status_code, 200)
        key = self.orchestrator._session_key(OPS_USER_ID, OPS_CHANNEL)
        self.assertEqual(self.orchestrator.conversation_histories[key], [])

    def test_outbound_page_hosts_the_console(self):
        html = self.client.get("/admin/outbound").get_data(as_text=True)
        self.assertIn("ops-form", html)
        self.assertIn("/admin/ops/ask", html)
