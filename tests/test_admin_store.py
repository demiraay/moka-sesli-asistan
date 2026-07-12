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
from core.admin_store import AdminStore
from core.orchestrator import AgentOrchestrator


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

    def test_update_listing_changes_inventory_and_price(self):
        self.store.update_listing(
            "INV-0001",
            {
                "block_id": "C",
                "floor": "9",
                "door_number": "901",
                "flat_type_id": "FT-3P1",
                "status": "reserved",
                "direction": "East",
                "list_price_try": "12345000",
                "vat_included": "true",
                "valid_until": "2027-01-01",
                "sun_exposure": "medium",
                "sun_hours_per_day": "4-6",
                "description": "Guncel ilan",
                "listing_description": "Kose daire, genis balkon",
            },
        )

        listing = self.store.get_listing("INV-0001")
        self.assertEqual(listing["block_id"], "C")
        self.assertEqual(listing["floor"], 9)
        self.assertEqual(listing["status"], "reserved")
        self.assertEqual(listing["list_price_try"], 12345000)
        self.assertEqual(listing["sunlight"]["sun_exposure"], "medium")
        self.assertEqual(listing["sunlight"]["description"], "Guncel ilan")
        self.assertEqual(listing["description"], "Kose daire, genis balkon")

        # listing_description gonderilmezse mevcut aciklama korunur (geriye uyumluluk).
        self.store.update_listing(
            "INV-0001",
            {
                "block_id": "C",
                "floor": "9",
                "door_number": "901",
                "flat_type_id": "FT-3P1",
                "status": "reserved",
                "direction": "East",
                "list_price_try": "12345000",
                "vat_included": "true",
                "valid_until": "2027-01-01",
                "sun_exposure": "medium",
                "sun_hours_per_day": "4-6",
                "description": "Guncel ilan",
            },
        )
        self.assertEqual(self.store.get_listing("INV-0001")["description"], "Kose daire, genis balkon")

    def test_sales_velocity_and_type_pressure(self):
        # Baslangicta satis verisi yok -> has_data False, durust bos-durum
        velocity = self.store.get_sales_velocity()
        self.assertFalse(velocity["has_data"])
        self.assertEqual(velocity["sold_last_n"], 0)

        # Iki satis logla -> hiz hesaplanir, absorpsiyon pozitif
        self.store.update_listing_status("INV-0001", "sold", source="panel")
        self.store.update_listing_status("INV-0003", "sold", source="panel")
        velocity = self.store.get_sales_velocity()
        self.assertTrue(velocity["has_data"])
        self.assertEqual(velocity["sold_last_n"], 2)
        self.assertIsNotNone(velocity["absorption_months"])

        # Talep-stok baskisi: 2+1 isteyen lead -> pozitif talep
        self.store.save_user_ai_notes("+905551112233", "2+1 ariyor", {"preferred_flat_type": "2+1"})
        pressure = self.store.get_type_pressure()
        row_2p1 = next((r for r in pressure if r["label"] == "2+1"), None)
        self.assertIsNotNone(row_2p1)
        self.assertEqual(row_2p1["demand"], 1)
        self.assertGreater(row_2p1["stock"], 0)

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

    def test_status_log_records_all_paths(self):
        def log_rows():
            with self.store._connect() as conn:
                return [dict(r) for r in conn.execute(
                    "SELECT inventory_id, from_status, to_status, source FROM listing_status_log ORDER BY id"
                ).fetchall()]

        # 1) Panodan tek tik durum degisikligi
        self.store.update_listing_status("INV-0001", "sold", source="panel")
        rows = log_rows()
        self.assertEqual(rows[-1], {"inventory_id": "INV-0001", "from_status": "available", "to_status": "sold", "source": "panel"})

        # 2) Ayni duruma set loglanmaz (gurultu yok)
        before = len(log_rows())
        self.store.update_listing_status("INV-0001", "sold", source="panel")
        self.assertEqual(len(log_rows()), before)

        # 3) Opsiyon koyma -> reserved (source=option); serbest birakma -> available (source=release)
        self.store.place_option("INV-0003", user_id="+905551112233", hours=48)
        self.store.release_reservation("INV-0003")
        sources = [r["source"] for r in log_rows()]
        self.assertIn("option", sources)
        self.assertIn("release", sources)

        # 4) Edit formundan durum degisikligi (source=edit)
        listing = self.store.get_listing("INV-0005")
        form = {"block_id": listing["block_id"], "floor": str(listing["floor"]), "door_number": listing["door_number"],
                "flat_type_id": listing["flat_type_id"], "status": "sold", "direction": listing["direction"],
                "list_price_try": str(listing["list_price_try"]), "vat_included": "true", "valid_until": "",
                "sun_exposure": "high", "sun_hours_per_day": "6-8", "description": ""}
        self.store.update_listing("INV-0005", form)
        self.assertTrue(any(r["source"] == "edit" and r["inventory_id"] == "INV-0005" for r in log_rows()))

    def test_stock_board_enrichment(self):
        board = self.store.get_stock_board()

        # Ust duzey ozetler
        self.assertIn("type_summary", board)
        self.assertTrue(board["type_summary"])
        first = board["type_summary"][0]
        self.assertIn("available", first)
        self.assertIn("total", first)
        self.assertGreaterEqual(first["total"], first["available"])
        self.assertIn("directions", board)
        self.assertIn("sun_exposures", board)
        self.assertGreater(board["price_range"]["max"], board["price_range"]["min"])
        self.assertIsInstance(board["expiring_options"], list)

        # Hucre alanlari: m², kat sayaci, fiyat/talep bandi
        sample_unit = board["blocks"][0]["floors"][0]["units"][0]
        self.assertIn("net_m2", sample_unit)
        self.assertIn("gross_m2", sample_unit)
        self.assertIn("price_band", sample_unit)
        self.assertIn("demand_band", sample_unit)
        row = board["blocks"][0]["floors"][0]
        self.assertIn("counts", row)
        self.assertEqual(row["counts"]["total"], len(row["units"]))

        # Yakinda dolan opsiyon expiring_options'a dusmeli
        available = next(
            u["inventory_id"]
            for block in board["blocks"] for floor in block["floors"] for u in floor["units"]
            if u["status"] == "available"
        )
        self.store.place_option(available, user_id="+905551112233", hours=12)
        board2 = self.store.get_stock_board()
        expiring_ids = {item["inventory_id"] for item in board2["expiring_options"]}
        self.assertIn(available, expiring_ids)

    def test_list_listings_filters_and_sort(self):
        available_2p1 = self.store.list_listings(filters={"flat_type_id": "FT-2P1", "status": "available"})
        self.assertTrue(available_2p1)
        self.assertTrue(all(l["flat_type_id"] == "FT-2P1" and l["status"] == "available" for l in available_2p1))

        block_a = self.store.list_listings(filters={"block_id": "A"})
        self.assertTrue(block_a)
        self.assertTrue(all(l["block_id"] == "A" for l in block_a))

        banded = self.store.list_listings(filters={"price_min": 8_000_000, "price_max": 10_000_000})
        self.assertTrue(banded)
        self.assertTrue(all(8_000_000 <= l["list_price_try"] <= 10_000_000 for l in banded))

        by_price = self.store.list_listings(sort="price_desc")
        prices = [l["list_price_try"] for l in by_price if l["list_price_try"] is not None]
        self.assertEqual(prices, sorted(prices, reverse=True))

        by_floor = self.store.list_listings(sort="floor_asc")
        floors = [l["floor"] for l in by_floor]
        self.assertEqual(floors, sorted(floors))

        # Geriye uyumluluk: filtresiz cagri eski davranisla ayni sonucu verir.
        self.assertEqual(
            [l["inventory_id"] for l in self.store.list_listings()],
            [l["inventory_id"] for l in self.store.list_listings("", None, None)],
        )

    FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32

    def test_listing_photos_inheritance_and_cover(self):
        listing = self.store.get_listing("INV-0001")

        # Foto yokken bos set doner.
        bundle = self.store.get_photos_for_listing(listing)
        self.assertEqual(bundle, {"photos": [], "source": None})

        # Tip fotografi yuklenince daire onu devralir; ilk foto otomatik kapak olur.
        type_photo_id = self.store.add_listing_photo("flat_type", listing["flat_type_id"], self.FAKE_JPEG, "jpg")
        bundle = self.store.get_photos_for_listing(listing)
        self.assertEqual(bundle["source"], "flat_type")
        self.assertEqual(len(bundle["photos"]), 1)
        self.assertTrue(bundle["photos"][0]["is_cover"])
        self.assertTrue((self.store.uploads_dir / bundle["photos"][0]["relpath"]).exists())

        # Daireye ozel foto tip setini ezer.
        unit_photo_id = self.store.add_listing_photo("unit", "INV-0001", self.FAKE_JPEG, "jpg")
        second_unit_photo = self.store.add_listing_photo("unit", "INV-0001", self.FAKE_JPEG, "png")
        bundle = self.store.get_photos_for_listing(listing)
        self.assertEqual(bundle["source"], "unit")
        self.assertEqual(len(bundle["photos"]), 2)

        # Kapak degistirme + kapak haritasi.
        self.store.set_cover_photo(second_unit_photo)
        cover_map = self.store.get_cover_photo_map()
        self.assertIn("INV-0001", cover_map["unit"])
        self.assertIn(listing["flat_type_id"], cover_map["flat_type"])

        # Kapak silinince kalan foto kapaga terfi eder, dosya diskten kalkar.
        photos = self.store.list_photos("unit", "INV-0001")
        cover_row = next(p for p in photos if p["is_cover"])
        self.store.delete_photo(cover_row["id"])
        remaining = self.store.list_photos("unit", "INV-0001")
        self.assertEqual(len(remaining), 1)
        self.assertTrue(remaining[0]["is_cover"])
        self.assertFalse((self.store.uploads_dir / cover_row["relpath"]).exists())

        # Ilan silinince unit fotolari ve klasoru temizlenir.
        self.store.delete_listing("INV-0001")
        self.assertEqual(self.store.list_photos("unit", "INV-0001"), [])
        self.assertFalse((self.store.uploads_dir / "INV-0001").exists())
        self.assertNotEqual(self.store.list_photos("flat_type", listing["flat_type_id"]), [])

    def test_delete_listing_removes_related_records(self):
        self.store.delete_listing("INV-0001")

        inventory_ids = {item["inventory_id"] for item in json.loads((self.base_dir / "data" / "inventory.json").read_text())}
        price_ids = {item["inventory_id"] for item in json.loads((self.base_dir / "data" / "prices.json").read_text())}
        sunlight_ids = {item["inventory_id"] for item in json.loads((self.base_dir / "data" / "sunlight.json").read_text())}

        self.assertNotIn("INV-0001", inventory_ids)
        self.assertNotIn("INV-0001", price_ids)
        self.assertNotIn("INV-0001", sunlight_ids)

    def test_admin_panel_routes_render(self):
        self.store.log_turn(
            session_id="session-2",
            user_id="user-2",
            channel="default",
            user_input="fiyatlar ne",
            agent_response="Fiyatlar 5 milyon 660 bin TL'den basliyor.",
            router_decision={"tool": "search_inventory", "args": {"status": "available"}},
            context={"message_facts": ["Inventory Search"], "units": [], "alternatives": [], "price_info": {"summary": "test", "count": 1}, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}},
        )

        app = create_app(store=self.store)
        client = app.test_client()

        dashboard = client.get("/admin")
        listings = client.get("/admin/listings")
        conversations = client.get("/admin/conversations")
        whatsapp_only = client.get("/admin/conversations?channel=whatsapp")
        detail = client.get("/admin/conversations/session-2")
        user_detail = client.get("/admin/users/user-2/conversations")
        user_detail_whatsapp = client.get("/admin/users/user-2/conversations?channel=whatsapp")
        sales_profile = client.get("/admin/sales-profile")
        filtered = client.get("/admin/conversations?q=fiyat&price_only=1")
        notes_update = client.post("/admin/users/user-2/notes", data={"manual_notes": "Aranacak"}, follow_redirects=False)
        sales_profile_update = client.post(
            "/admin/sales-profile",
            data={
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
            },
            follow_redirects=False,
        )

        self.assertEqual(dashboard.status_code, 200)
        self.assertEqual(listings.status_code, 200)
        self.assertEqual(conversations.status_code, 200)
        self.assertEqual(whatsapp_only.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(user_detail.status_code, 200)
        self.assertEqual(user_detail_whatsapp.status_code, 200)
        self.assertEqual(sales_profile.status_code, 200)
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(notes_update.status_code, 302)
        self.assertEqual(sales_profile_update.status_code, 302)

    def test_admin_app_exposes_whatsapp_routes_in_same_process(self):
        orchestrator = AgentOrchestrator()
        orchestrator.admin_store = self.store
        orchestrator.llm_client.generate = MagicMock(side_effect=[
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

    def test_stock_board_groups_units_by_block_and_floor(self):
        board = self.store.get_stock_board()

        self.assertEqual(len(board["blocks"]), 5)
        self.assertEqual(board["totals"]["total"], 212)
        self.assertEqual(
            board["totals"]["total"],
            board["totals"]["available"] + board["totals"]["reserved"] + board["totals"]["sold"],
        )

        block_a = board["blocks"][0]
        self.assertEqual(block_a["block_id"], "A")
        # Katlar azalan sirada (ust kat en ustte)
        floors = [row["floor"] for row in block_a["floors"]]
        self.assertEqual(floors, sorted(floors, reverse=True))

        first_unit = block_a["floors"][-1]["units"][0]
        self.assertIn("inventory_id", first_unit)
        self.assertIn(first_unit["status"], ("available", "reserved", "sold"))
        self.assertTrue(first_unit["price_short"].endswith("M"))

    def test_update_listing_status_changes_only_status(self):
        listing_before = self.store.get_listing("INV-0001")

        self.store.update_listing_status("INV-0001", "reserved")

        listing_after = self.store.get_listing("INV-0001")
        self.assertEqual(listing_after["status"], "reserved")
        self.assertEqual(listing_after["list_price_try"], listing_before["list_price_try"])
        self.assertEqual(listing_after["door_number"], listing_before["door_number"])

        with self.assertRaises(ValueError):
            self.store.update_listing_status("INV-0001", "kaporali")
        with self.assertRaises(KeyError):
            self.store.update_listing_status("INV-9999", "sold")

    def test_stock_routes_render_and_update_status(self):
        app = create_app(store=self.store)
        client = app.test_client()

        page = client.get("/admin/stock")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Stok Panosu", page.get_data(as_text=True))

        response = client.post("/admin/stock/INV-0002/status", json={"status": "sold"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.get_listing("INV-0002")["status"], "sold")

        bad = client.post("/admin/stock/INV-0002/status", json={"status": "yok-boyle-durum"})
        self.assertEqual(bad.status_code, 400)

    def test_set_lead_stage_creates_row_and_records_event(self):
        # Notu olmayan kullanici icin de asama atanabilmeli
        self.store.set_lead_stage("+905550001111", "qualified")

        notes = self.store.get_user_ai_notes("+905550001111")
        self.assertEqual(notes["stage"], "qualified")

        events = self.store.get_lead_events("+905550001111")
        self.assertEqual(events[0]["event_type"], "stage_change")
        self.assertEqual(events[0]["payload"], {"from": "new", "to": "qualified"})

        with self.assertRaises(ValueError):
            self.store.set_lead_stage("+905550001111", "uydurma-asama")

    def test_set_lead_stage_preserves_existing_notes(self):
        self.store.save_user_ai_notes(
            user_id="+905550002222",
            ai_summary="3+1 ariyor",
            ai_notes={"preferred_flat_type": "3+1"},
        )

        self.store.set_lead_stage("+905550002222", "appointment")

        notes = self.store.get_user_ai_notes("+905550002222")
        self.assertEqual(notes["stage"], "appointment")
        self.assertEqual(notes["ai_summary"], "3+1 ariyor")
        self.assertEqual(notes["ai_notes"]["preferred_flat_type"], "3+1")

        # AI notu guncellenince asama korunmali
        self.store.save_user_ai_notes(
            user_id="+905550002222",
            ai_summary="3+1 ariyor, butce netlesti",
            ai_notes={"preferred_flat_type": "3+1", "budget_max_try": 12_000_000},
        )
        self.assertEqual(self.store.get_user_ai_notes("+905550002222")["stage"], "appointment")

    def test_get_leads_merges_sessions_and_notes(self):
        base_context = {"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}}
        self.store.log_turn(
            session_id="lead-s1", user_id="+905550003333", channel="whatsapp",
            user_input="2+1 var mi", agent_response="Var",
            router_decision={"tool": "search_inventory", "args": {}}, context=base_context,
        )
        self.store.save_user_ai_notes(
            user_id="+905550003333",
            ai_summary="2+1 istiyor, ziyaret talebi",
            ai_notes={"handoff_required": True, "budget_max_try": 9_000_000, "preferred_flat_type": "2+1"},
        )
        # Panel test kullanicisi lead listesinde gorunmemeli
        self.store.log_turn(
            session_id="lead-s2", user_id="panel-test", channel="panel",
            user_input="deneme", agent_response="Tamam",
            router_decision={"tool": "answer_general", "args": {}}, context=base_context,
        )

        leads = self.store.get_leads()

        user_ids = [lead["user_id"] for lead in leads]
        self.assertIn("+905550003333", user_ids)
        self.assertNotIn("panel-test", user_ids)

        lead = next(item for item in leads if item["user_id"] == "+905550003333")
        self.assertEqual(lead["stage"], "new")
        self.assertEqual(lead["temperature"], "hot")
        self.assertEqual(lead["suggested_stage"], "appointment")
        self.assertEqual(lead["conversation_count"], 1)
        self.assertTrue(lead["handoff_required"])

    def test_leads_routes_render_and_update_stage(self):
        base_context = {"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}}
        self.store.log_turn(
            session_id="lead-s3", user_id="+905550004444", channel="whatsapp",
            user_input="merhaba", agent_response="Merhaba",
            router_decision={"tool": "answer_general", "args": {}}, context=base_context,
        )

        app = create_app(store=self.store)
        client = app.test_client()

        page = client.get("/admin/leads")
        self.assertEqual(page.status_code, 200)
        body = page.get_data(as_text=True)
        self.assertIn("Musteri Adaylari", body)
        self.assertIn("+905550004444", body)

        response = client.post("/admin/leads/+905550004444/stage", json={"stage": "visited"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.get_user_ai_notes("+905550004444")["stage"], "visited")

        bad = client.post("/admin/leads/+905550004444/stage", json={"stage": "yok"})
        self.assertEqual(bad.status_code, 400)

    def test_option_lifecycle_reserves_and_releases_unit(self):
        self.store.place_option("INV-0001", user_id="+905550008888", hours=48, note="aksam gelecek")

        self.assertEqual(self.store.get_listing("INV-0001")["status"], "reserved")
        reservation = self.store.get_active_reservations()["INV-0001"]
        self.assertEqual(reservation["kind"], "option")
        self.assertEqual(reservation["user_id"], "+905550008888")
        self.assertIsNotNone(reservation["remaining_label"])

        # Lead asamasi otomatik 'reserved'a tasinmali
        self.assertEqual(self.store.get_user_ai_notes("+905550008888")["stage"], "reserved")

        # Ayni daireye ikinci opsiyon konamaz
        with self.assertRaises(ValueError):
            self.store.place_option("INV-0001", hours=24)

        self.store.release_reservation("INV-0001", note="vazgecti")
        self.assertEqual(self.store.get_listing("INV-0001")["status"], "available")
        self.assertNotIn("INV-0001", self.store.get_active_reservations())

    def test_expired_option_auto_releases(self):
        self.store.place_option("INV-0003", user_id="+905550009999", hours=0)

        released = self.store.release_expired_options()

        self.assertEqual(released, 1)
        self.assertEqual(self.store.get_listing("INV-0003")["status"], "available")
        self.assertNotIn("INV-0003", self.store.get_active_reservations())
        event_types = [event["event_type"] for event in self.store.get_lead_events("+905550009999")]
        self.assertIn("option_expired", event_types)

    def test_deposit_converts_option_and_survives(self):
        self.store.place_option("INV-0005", user_id="+905550001010", hours=48)

        self.store.place_deposit("INV-0005", user_id="+905550001010", amount_try=250_000, note="dekont geldi")

        reservation = self.store.get_active_reservations()["INV-0005"]
        self.assertEqual(reservation["kind"], "deposit")
        self.assertEqual(reservation["amount_try"], 250_000)
        self.assertIsNone(reservation["remaining_label"])
        self.assertEqual(self.store.get_listing("INV-0005")["status"], "reserved")

        # Kaporali daire suresi dolan opsiyon gibi dusmemeli
        self.assertEqual(self.store.release_expired_options(), 0)

        # Satisa cevrilince rezervasyon kapanir
        self.store.update_listing_status("INV-0005", "sold")
        self.assertNotIn("INV-0005", self.store.get_active_reservations())

    def test_stock_board_includes_reservation_info(self):
        self.store.place_option("INV-0007", user_id="+905550001212", hours=24)

        board = self.store.get_stock_board()
        block_a = next(block for block in board["blocks"] if block["block_id"] == "A")
        unit = next(
            unit
            for row in block_a["floors"]
            for unit in row["units"]
            if unit["inventory_id"] == "INV-0007"
        )

        self.assertEqual(unit["status"], "reserved")
        self.assertEqual(unit["reservation"]["kind"], "option")

    def test_reservation_routes(self):
        app = create_app(store=self.store)
        client = app.test_client()

        response = client.post("/admin/stock/INV-0009/option", json={"user_id": "+905550001313", "hours": 24})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.store.get_listing("INV-0009")["status"], "reserved")

        # Rezerve daireye tekrar opsiyon 400 donmeli
        again = client.post("/admin/stock/INV-0009/option", json={"hours": 24})
        self.assertEqual(again.status_code, 400)

        deposit = client.post("/admin/stock/INV-0009/deposit", json={"user_id": "+905550001313", "amount_try": "150000"})
        self.assertEqual(deposit.status_code, 200)
        self.assertEqual(self.store.get_active_reservations()["INV-0009"]["kind"], "deposit")

        release = client.post("/admin/stock/INV-0009/release", json={})
        self.assertEqual(release.status_code, 200)
        self.assertEqual(self.store.get_listing("INV-0009")["status"], "available")

    def test_briefing_context_and_storage(self):
        context = self.store.get_briefing_context()
        for key in ("kpis", "stok", "sicak_leadler", "insan_bekleyenler", "takip_listesi", "son_aktivite"):
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
        self.assertIn("sicak_leadler", kwargs["user_prompt"])

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
        orchestrator.llm_client.generate = MagicMock(return_value="- Sakin bir gun; 145 daire satista.")

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

    def test_followup_tasks_combine_three_sources(self):
        from datetime import datetime, timedelta, timezone

        base_context = {"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}}
        handoff_context = dict(base_context, handoff={"required": True, "reason": "Visit", "missing_info": [], "share_contact_details": True, "share_location": False})

        # 1) Bekleyen handoff
        self.store.log_turn(
            session_id="ft-1", user_id="+905550007070", channel="whatsapp",
            user_input="ofise gelmek istiyorum", agent_response="Yonlendiriyorum",
            router_decision={"tool": "trigger_handoff", "args": {}}, context=handoff_context,
        )
        # 2) 24 saat icinde dolacak opsiyon
        self.store.place_option("INV-0003", user_id="+905550008080", hours=5)
        # 3) 4 gundur temassiz sicak lead
        old = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
        self.store.log_turn(
            session_id="ft-2", user_id="+905550009090", channel="whatsapp",
            user_input="2+1 bakiyorum", agent_response="Tabii",
            router_decision={"tool": "search_inventory", "args": {}}, context=base_context,
            created_at=old,
        )
        self.store.save_user_ai_notes(
            user_id="+905550009090", ai_summary="2+1, 9M butce, acil",
            ai_notes={"budget_max_try": 9_000_000, "preferred_flat_type": "2+1", "urgency": "high", "handoff_required": True},
        )
        # Devralinca handoff gorevi dusmeli ama temassizlik gorevi kalabilir
        self.store.claim_handoff("+905550009090")

        tasks = self.store.get_followup_tasks()
        kinds = {(task["kind"], task["user_id"]) for task in tasks}

        self.assertIn(("handoff", "+905550007070"), kinds)
        self.assertIn(("option", "+905550008080"), kinds)
        self.assertIn(("stale_lead", "+905550009090"), kinds)
        # Oncelik sirasi: high olanlar basta
        self.assertEqual(tasks[0]["priority"], "high")

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

    def test_analytics_report_funnel_and_ai_performance(self):
        base_context = {"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}}
        handoff_context = dict(base_context, handoff={"required": True, "reason": "Visit", "missing_info": [], "share_contact_details": True, "share_location": False})

        # 2 oturum: biri handoff'lu, biri degil
        self.store.log_turn(
            session_id="an-1", user_id="+905550003030", channel="whatsapp",
            user_input="2+1 var mi", agent_response="Var",
            router_decision={"tool": "search_inventory", "args": {}}, context=base_context,
        )
        self.store.log_turn(
            session_id="an-2", user_id="+905550004040", channel="whatsapp",
            user_input="ofise gelmek istiyorum", agent_response="Yonlendiriyorum",
            router_decision={"tool": "trigger_handoff", "args": {}}, context=handoff_context,
        )
        self.store.save_user_ai_notes(
            user_id="+905550003030", ai_summary="2+1",
            ai_notes={"preferred_flat_type": "2+1", "preferred_direction": "South"},
        )
        self.store.set_lead_stage("+905550003030", "qualified")
        self.store.set_lead_stage("+905550004040", "reserved")

        report = self.store.get_analytics_report()

        funnel = {step["stage"]: step["count"] for step in report["funnel"]}
        self.assertEqual(funnel["new"], 2)
        self.assertEqual(funnel["qualified"], 2)   # qualified + reserved
        self.assertEqual(funnel["reserved"], 1)
        self.assertEqual(funnel["won"], 0)

        type_row = next(row for row in report["demand_by_type"] if row["label"] == "2+1")
        self.assertEqual(type_row["demand"], 1)
        self.assertGreater(type_row["stock"], 0)
        self.assertGreater(type_row["pressure"], 0)

        perf = report["ai_performance"]
        self.assertEqual(perf["total_sessions"], 2)
        self.assertEqual(perf["handoff_sessions"], 1)
        self.assertEqual(perf["handoff_rate_pct"], 50)
        self.assertEqual(perf["containment_pct"], 50)

        tools = {row["tool"]: row["count"] for row in report["tool_usage"]}
        self.assertEqual(tools["search_inventory"], 1)
        self.assertEqual(tools["trigger_handoff"], 1)

    def test_analytics_page_and_csv_exports(self):
        base_context = {"message_facts": [], "units": [], "alternatives": [], "price_info": None, "next_questions": [], "handoff": {"required": False, "reason": "", "missing_info": []}}
        self.store.log_turn(
            session_id="an-3", user_id="+905550005050", channel="whatsapp",
            user_input="merhaba", agent_response="Merhaba",
            router_decision={"tool": "answer_general", "args": {}}, context=base_context,
        )

        app = create_app(store=self.store)
        client = app.test_client()

        page = client.get("/admin/analytics")
        self.assertEqual(page.status_code, 200)
        self.assertIn("Donusum Hunisi", page.get_data(as_text=True))

        leads_csv = client.get("/admin/analytics/export/leads.csv")
        self.assertEqual(leads_csv.status_code, 200)
        self.assertIn("text/csv", leads_csv.content_type)
        body = leads_csv.get_data(as_text=True)
        self.assertIn("user_id", body.splitlines()[0])
        self.assertIn("+905550005050", body)

        conv_csv = client.get("/admin/analytics/export/conversations.csv")
        self.assertEqual(conv_csv.status_code, 200)
        self.assertIn("an-3", conv_csv.get_data(as_text=True))

    def test_offer_flow_creates_record_and_prints(self):
        app = create_app(store=self.store)
        client = app.test_client()

        form = client.get("/admin/offers/new?inventory_id=INV-0001")
        self.assertEqual(form.status_code, 200)
        self.assertIn("Teklif Olustur", form.get_data(as_text=True))

        preview = client.post("/admin/offers/new", data={
            "inventory_id": "INV-0001",
            "user_id": "+905550002020",
            "down_payment_pct": "30",
            "months": "36",
            "balloon_count": "2",
            "balloon_amount": "500000",
            "annual_rate_pct": "0",
            "action": "preview",
        })
        self.assertEqual(preview.status_code, 200)
        self.assertIn("Plan Ozeti", preview.get_data(as_text=True))

        save = client.post("/admin/offers/new", data={
            "inventory_id": "INV-0001",
            "user_id": "+905550002020",
            "down_payment_pct": "30",
            "months": "36",
            "balloon_count": "2",
            "balloon_amount": "500000",
            "annual_rate_pct": "0",
            "action": "save",
        }, follow_redirects=False)
        self.assertEqual(save.status_code, 302)
        offer_id = int(save.headers["Location"].rstrip("/").split("/")[-1])

        offer = self.store.get_offer(offer_id)
        self.assertEqual(offer["inventory_id"], "INV-0001")
        self.assertEqual(offer["plan"]["inputs"]["months"], 36)
        listing_price = self.store.get_listing("INV-0001")["list_price_try"]
        self.assertEqual(offer["plan"]["total"], listing_price)

        print_page = client.get(f"/admin/offers/{offer_id}")
        self.assertEqual(print_page.status_code, 200)
        self.assertIn("Odeme Plani Teklifi", print_page.get_data(as_text=True))

        event_types = [event["event_type"] for event in self.store.get_lead_events("+905550002020")]
        self.assertIn("offer_created", event_types)

    def test_unit_matches_scores_by_preferences(self):
        notes = {
            "preferred_flat_type_id": "FT-2P1",
            "preferred_flat_type": "2+1",
            "budget_max_try": 9_000_000,
            "preferred_direction": "South-West",
        }

        matches = self.store.get_unit_matches(notes)

        self.assertTrue(matches)
        self.assertLessEqual(len(matches), 3)
        top = matches[0]
        self.assertEqual(top["flat_label"], "2+1")
        self.assertLessEqual(top["list_price_try"], 9_000_000)
        self.assertGreaterEqual(top["score"], 7)
        self.assertIn("bütçeye uygun", top["reasons"])
        # Skorlar azalan sirali olmali
        scores = [match["score"] for match in matches]
        self.assertEqual(scores, sorted(scores, reverse=True))

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

        self.assertEqual(analytics["demand_series"][0]["label"], "2+1")
        top_lead = analytics["hot_leads"][0]
        self.assertEqual(top_lead["user_id"], "u-night")
        self.assertEqual(top_lead["temperature"], "hot")
        self.assertTrue(top_lead["handoff_required"])

        inventory = analytics["inventory"]
        self.assertEqual(inventory["total"], inventory["available"] + inventory["reserved"] + inventory["sold"])
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
        orchestrator.llm_client.generate = MagicMock(side_effect=[
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
        orchestrator.llm_client.generate = MagicMock(
            return_value='{"tool": "answer_general", "args": {"category": "other"}}'
        )

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
