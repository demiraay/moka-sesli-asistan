import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import whatsapp.app as whatsapp_app_module
from conftest import stub_llm_script
from core.admin_store import AdminStore
from core.orchestrator import AgentOrchestrator


class TestWhatsAppBridge(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.temp_dir.name)
        shutil.copytree(Path(__file__).resolve().parent.parent / "data", self.base_dir / "data")
        self.store = AdminStore(
            base_dir=str(self.base_dir),
            db_path=str(self.base_dir / "admin_test.sqlite3"),
        )
        self.orchestrator = AgentOrchestrator()
        self.orchestrator.admin_store = self.store
        whatsapp_app_module.orchestrator = self.orchestrator
        self.app = whatsapp_app_module.create_app()
        self.client = self.app.test_client()

    def tearDown(self):
        os.environ.pop("WHATSAPP_BRIDGE_TOKEN", None)
        self.temp_dir.cleanup()

    def test_message_endpoint_requires_token_when_configured(self):
        os.environ["WHATSAPP_BRIDGE_TOKEN"] = "gizli-token"
        stub_llm_script(self.orchestrator.llm_client, [
            '{"tool": "answer_general", "args": {"category": "greeting"}}',
            "Merhaba, ben Ekinciler Residence satış danışmanınız. Size nasıl yardımcı olabilirim?",
        ])

        missing = self.client.post(
            "/whatsapp/message",
            json={"phone_number": "+905551112233", "message": "selam"},
        )
        self.assertEqual(missing.status_code, 401)

        wrong = self.client.post(
            "/whatsapp/message",
            json={"phone_number": "+905551112233", "message": "selam"},
            headers={"X-Bridge-Token": "yanlis-token"},
        )
        self.assertEqual(wrong.status_code, 401)

        valid = self.client.post(
            "/whatsapp/message",
            json={"phone_number": "+905551112233", "message": "selam"},
            headers={"X-Bridge-Token": "gizli-token"},
        )
        self.assertEqual(valid.status_code, 200)

    def test_message_endpoint_accepts_bearer_token(self):
        os.environ["WHATSAPP_BRIDGE_TOKEN"] = "gizli-token"
        stub_llm_script(self.orchestrator.llm_client, [
            '{"tool": "answer_general", "args": {"category": "greeting"}}',
            "Merhaba, ben Ekinciler Residence satış danışmanınız. Size nasıl yardımcı olabilirim?",
        ])

        response = self.client.post(
            "/whatsapp/message",
            json={"phone_number": "+905551112233", "message": "selam"},
            headers={"Authorization": "Bearer gizli-token"},
        )
        self.assertEqual(response.status_code, 200)

    def test_health_endpoint_stays_open_with_token_configured(self):
        os.environ["WHATSAPP_BRIDGE_TOKEN"] = "gizli-token"

        response = self.client.get("/whatsapp/health")
        self.assertEqual(response.status_code, 200)

    def test_whatsapp_message_uses_phone_number_as_user_id(self):
        stub_llm_script(self.orchestrator.llm_client, [
            '{"tool": "answer_general", "args": {"category": "greeting"}}',
            "Merhaba, ben Ekinciler Residence satış danışmanınız. Size nasıl yardımcı olabilirim?",
        ])

        response = self.client.post(
            "/whatsapp/message",
            json={"phone_number": "+905551112233", "message": "selam"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["user_id"], "+905551112233")
        self.assertEqual(payload["phone_number"], "+905551112233")
        self.assertIn("Ekinciler Residence", payload["reply"])

        notes = self.store.get_user_ai_notes("+905551112233")
        self.assertEqual(notes["user_id"], "+905551112233")

    def test_whatsapp_phone_number_is_normalized_for_turkish_variants(self):
        stub_llm_script(self.orchestrator.llm_client, [
            '{"tool": "answer_general", "args": {"category": "greeting"}}',
            "Merhaba, ben Ekinciler Residence satış danışmanınız. Size nasıl yardımcı olabilirim?",
        ])

        response = self.client.post(
            "/whatsapp/message",
            json={"phone_number": "0540", "message": "selam"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["phone_number"], "+90540")

    def test_whatsapp_lid_sender_is_sanitized_before_becoming_user_id(self):
        stub_llm_script(self.orchestrator.llm_client, [
            '{"tool": "answer_general", "args": {"category": "greeting"}}',
            "Merhaba, ben Ekinciler Residence satış danışmanınız. Size nasıl yardımcı olabilirim?",
        ])

        response = self.client.post(
            "/whatsapp/message",
            json={"phone_number": "15733086019665@lid", "message": "selam"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["user_id"], "+15733086019665")
        self.assertEqual(payload["phone_number"], "+15733086019665")

    def test_whatsapp_international_sender_is_normalized_to_canonical_user_id(self):
        stub_llm_script(self.orchestrator.llm_client, [
            '{"tool": "answer_general", "args": {"category": "greeting"}}',
            "Merhaba, ben Ekinciler Residence satış danışmanınız. Size nasıl yardımcı olabilirim?",
        ])

        response = self.client.post(
            "/whatsapp/message",
            json={"phone_number": "84379733489", "message": "selam"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["user_id"], "+84379733489")
        self.assertEqual(payload["phone_number"], "+84379733489")

        notes = self.store.get_user_ai_notes("+84379733489")
        self.assertEqual(notes["user_id"], "+84379733489")

    def test_paused_user_gets_no_ai_reply_but_message_is_logged(self):
        self.store.set_ai_paused("+905551112233", True)
        bomb = MagicMock(side_effect=AssertionError("AI duraklatilmisken LLM cagrilmamali"))
        self.orchestrator.llm_client.generate = bomb
        self.orchestrator.llm_client.chat = bomb

        response = self.client.post(
            "/whatsapp/message",
            json={"phone_number": "+905551112233", "message": "hala orada misiniz?"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertIsNone(payload["reply"])
        self.assertTrue(payload["paused"])
        self.assertEqual(payload["follow_up_actions"], [])

        # Mesaj gecmise islenmis olmali
        session_id = self.store.get_latest_session_id_for_user("+905551112233", "whatsapp")
        conversation = self.store.get_conversation(session_id)
        self.assertEqual(conversation["turns"][-1]["user_input"], "hala orada misiniz?")
        self.assertEqual(conversation["turns"][-1]["router_decision"]["tool"], "human_takeover")

    def test_outbox_endpoints_serve_and_ack_with_token(self):
        os.environ["WHATSAPP_BRIDGE_TOKEN"] = "gizli-token"
        outbox_id = self.store.enqueue_outbound_message("+905551112233", "Panelden yazildi")

        unauthorized = self.client.get("/whatsapp/outbox")
        self.assertEqual(unauthorized.status_code, 401)

        listing = self.client.get("/whatsapp/outbox", headers={"X-Bridge-Token": "gizli-token"})
        self.assertEqual(listing.status_code, 200)
        messages = listing.get_json()["messages"]
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]["message"], "Panelden yazildi")

        ack = self.client.post(
            f"/whatsapp/outbox/{outbox_id}/ack",
            json={"ok": True},
            headers={"X-Bridge-Token": "gizli-token"},
        )
        self.assertEqual(ack.status_code, 200)
        empty = self.client.get("/whatsapp/outbox", headers={"X-Bridge-Token": "gizli-token"})
        self.assertEqual(empty.get_json()["messages"], [])

    def test_whatsapp_handoff_returns_contact_text_without_auto_location_for_generic_redirect(self):
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
        stub_llm_script(self.orchestrator.llm_client, [
            '{"tool": "trigger_handoff", "args": {"reason": "Visit request", "share_contact_details": true}}',
            "Sizi satış danışmanımız Ayse Yilmaz'a yönlendiriyorum, numarasını iletiyorum.",
        ])

        response = self.client.post(
            "/whatsapp/message",
            json={"phone_number": "+905551112233", "message": "beni yonlendir"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["sales_profile"]["consultant_name"], "Ayse Yilmaz")
        self.assertTrue(any(item["type"] == "text" for item in payload["follow_up_actions"]))
        self.assertFalse(any(item["type"] == "location" for item in payload["follow_up_actions"]))


class TestExceptionToggle(unittest.TestCase):
    """Konusan kisiyi tek tikla sustur/ac + WhatsApp'ta AI'nin susmasi.

    Iki app kullanilir: admin paneli (toggle rotasi) ve WhatsApp koprusu
    (mesaj alan uc). Ikisi ayni admin_store'u paylasir.
    """

    def setUp(self):
        from admin_panel.app import create_app as create_admin_app
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.store = AdminStore(db_path=str(Path(self.temp_dir.name) / "exc.sqlite3"))
        self.orchestrator = AgentOrchestrator()
        self.orchestrator.admin_store = self.store
        self.orchestrator.conversation_histories.clear()
        self.orchestrator.user_profiles.clear()

        whatsapp_app_module.orchestrator = self.orchestrator
        self.bridge = whatsapp_app_module.create_app().test_client()

        admin = create_admin_app(store=self.store, orchestrator=self.orchestrator)
        admin.config["TESTING"] = True
        self.panel = admin.test_client()

    def _send(self, text, phone="35781909434522"):
        return self.bridge.post("/whatsapp/message",
                                json={"phone_number": phone, "message": text})

    def test_recent_contacts_lists_who_talked(self):
        stub_llm_script(self.orchestrator.llm_client, [
            '{"tool": "answer_general", "args": {"category": "greeting"}}', "Merhaba"])
        self._send("selam")

        # LID normalize edilir: 35781909434522 -> +35781909434522. Panelde
        # gorunmesi gereken de bu kanal-user_id'sidir.
        contacts = self.store.list_recent_contacts()
        ids = [c["user_id"] for c in contacts]
        self.assertIn("+35781909434522", ids)
        self.assertFalse(contacts[0]["blocked"])

    def test_toggle_blocks_then_ai_stays_silent(self):
        """EN ONEMLI: susturulan kisiye WhatsApp AI'si YANIT URETMEZ."""
        stub_llm_script(self.orchestrator.llm_client, [
            '{"tool": "answer_general", "args": {}}', "cevap"])
        self._send("ilk mesaj")           # kisi konusmus olsun

        # Panel akisi: listeden gelen kanal-user_id'si ile sustur.
        uid = self.store.list_recent_contacts()[0]["user_id"]
        response = self.panel.post(f"/admin/exceptions/{uid}/toggle",
                                   data={"display": "Test Kişi"})
        self.assertIn(response.status_code, (302, 303))
        self.assertTrue(self.store.is_blocked(uid))

        # Artik AI cevap URETMEMELI: LLM bir kez daha cagrilirsa patlar
        bomb = MagicMock(side_effect=AssertionError("susturulmus kisiye LLM cagrildi"))
        self.orchestrator.llm_client.chat = bomb
        self.orchestrator.llm_client.generate = bomb

        data = self._send("bir mesaj daha").get_json()
        self.assertTrue(data["blocked"])
        self.assertIsNone(data["reply"])

    def test_toggle_again_reopens(self):
        self.store.add_to_blocklist("+35781909434522", "test")
        self.panel.post("/admin/exceptions/+35781909434522/toggle",
                        data={"display": "Test"})
        self.assertFalse(self.store.is_blocked("+35781909434522"))

    def test_internal_users_are_not_listed_as_contacts(self):
        """panel-test (simulator) istisna listesinde gorunmemeli."""
        stub_llm_script(self.orchestrator.llm_client,
                        ['{"tool": "answer_general", "args": {}}', "ok"])
        self.orchestrator.process_turn("selam", user_id="panel-test", channel="panel")
        ids = [c["user_id"] for c in self.store.list_recent_contacts()]
        self.assertNotIn("panel-test", ids)
