import sys
import os
import tempfile
import unittest
from unittest.mock import MagicMock

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.admin_store import AdminStore
from core.orchestrator import AgentOrchestrator

class TestFullFlow(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.orchestrator = AgentOrchestrator()
        # Testler gercek data/admin.sqlite3'e yazmasin diye gecici DB kullan
        self.orchestrator.admin_store = AdminStore(
            db_path=os.path.join(self.temp_dir.name, "admin_test.sqlite3")
        )
        # Mock LLM to avoid real API calls during generic testing
        # We can implement a "real" mode later if needed
        self.orchestrator.llm_client.generate = MagicMock(return_value="[LLM] Based on my data, yes we have that.")
        self.orchestrator.admin_store.log_turn = MagicMock()
        self.orchestrator.admin_store.save_user_ai_notes = MagicMock()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_customer_card_tracks_preference_change_and_known_phone(self):
        """Agentic hafiza: router'in urettigi kart tercih degisimini yakalar,
        WhatsApp telefonu karta islenir ve LLM prompt'una 'zaten elinde' notu gider."""
        import json as _json

        user_id = "+905551112233"
        router_turn1 = _json.dumps({
            "tool": "search_inventory",
            "args": {"flat_type_id": "FT-3P1", "status": "available"},
            "card": {"flat_type": "3+1", "intent": "3+1 ariyor", "changed": ["3+1 istiyor"]},
        })
        router_turn2 = _json.dumps({
            "tool": "search_inventory",
            "args": {"flat_type_id": "FT-4P1", "status": "available"},
            "card": {"flat_type": "4+1", "intent": "4+1 ariyor",
                     "changed": ["daire tipi 3+1 -> 4+1 degisti"]},
        })
        self.orchestrator.llm_client.generate.side_effect = [
            router_turn1, "3+1 secenekleri var.",
            router_turn2, "4+1 gosteriyorum.",
        ]

        self.orchestrator.process_turn("3+1 daire ariyorum", user_id=user_id, channel="whatsapp")
        profile = self.orchestrator._get_user_profile(user_id)
        self.assertEqual(profile["card"]["flat_type"], "3+1")
        # WhatsApp'ta user_id telefondur; karta otomatik islenir.
        self.assertEqual(profile["phone_number"], user_id)
        self.assertEqual(profile["card"]["phone"], user_id)

        self.orchestrator.process_turn("yok aslinda 4+1 olsun", user_id=user_id, channel="whatsapp")
        profile = self.orchestrator._get_user_profile(user_id)
        # Kart eski tercihi degil GUNCEL tercihi tasir.
        self.assertEqual(profile["card"]["flat_type"], "4+1")

        # 2. turun generation cagrisina kart + telefon + DEGISTI notu gitmis olmali.
        generation_call = self.orchestrator.llm_client.generate.call_args_list[3]
        prompt_blob = (generation_call.kwargs.get("system_prompt", "") +
                       generation_call.kwargs.get("user_prompt", ""))
        self.assertIn("4+1", prompt_blob)
        self.assertIn("ZATEN ELINDE", prompt_blob)
        self.assertIn(user_id, prompt_blob)
        self.assertIn("DEGISTI", prompt_blob)

        # Bilgi envanteri: telefon/tip BILINEN'de, isim/butce EKSIK'te olmali —
        # LLM yeterlilik kontrolunu bu envanterden yapar.
        self.assertIn("ELIMDEKI BILGILER", prompt_blob)
        known_line = next(l for l in prompt_blob.splitlines() if l.startswith("- BILINEN:"))
        missing_line = next(l for l in prompt_blob.splitlines() if l.startswith("- EKSIK:"))
        self.assertIn("Telefon", known_line)
        self.assertIn("Daire tipi", known_line)
        self.assertIn("Isim", missing_line)
        self.assertNotIn("Telefon", missing_line)

        # Kart ai_notes'a da yazilir (kalicilik: restart'ta geri yuklenir).
        notes_call = self.orchestrator.admin_store.save_user_ai_notes.call_args
        self.assertEqual(notes_call.kwargs["ai_notes"]["card"]["flat_type"], "4+1")

    def test_availability_flow(self):
        """
        User asks for availability.
        Orchestrator should:
        1. Detect 'availability' intent.
        2. Detect slots (e.g. 3+1).
        3. Query inventory.
        4. Pass facts to LLM.
        """
        user_input = "3+1 daire var mı?"
        
        # Mock Router Response for test stability (unit test)
        # In a real E2E test we trust the LLM, but for unittesting logic we mock.
        # But wait, self.orchestrator.llm_client.generate is mocked. 
        # We need to simulate TWO calls: 1. Router, 2. Final Response.
        
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "search_inventory", "args": {"flat_type_id": "FT-3P1", "status": "available"}}', # Router
            "We have 3+1 flats available." # Final
        ]
        
        result = self.orchestrator.process_turn(user_input)
        
        self.assertEqual(result['router_decision']['tool'], 'search_inventory')
        self.assertEqual(result['router_decision']['args']['flat_type_id'], 'FT-3P1')
        
        # Check context facts
        context = result['context']
        # Depending on data, we expect either units or no units.
        # But 'message_facts' should contain something about finding units or not.
        self.assertTrue(len(context['message_facts']) > 0)
        
        # Verify LLM was called
        # Verify LLM was called twice (Router + Final)
        self.assertEqual(self.orchestrator.llm_client.generate.call_count, 2)
        print(f"Agent Response: {result['agent_response']}")

    def test_handoff_flow(self):
        """
        User asks for visit.
        Orchestrator should trigger handoff.
        """
        user_input = "Projeyi ziyaret etmek istiyorum."
        
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "trigger_handoff", "args": {"reason": "User requested visit"}}', # Router
            "Sure, I will connect you." # Final
        ]
        
        result = self.orchestrator.process_turn(user_input)
        
        self.assertTrue(result['context']['handoff']['required'])
        self.assertIn('visit', result['context']['handoff']['reason'])

    def test_price_flow(self):
        """
        User asks for price of A block.
        """
        user_input = "A blok fiyatlar ne?"
        
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "search_inventory", "args": {"block_id": "A"}}', # Router - likely mapped to search
            "Prices are 7.9M." # Final
        ]
        
        result = self.orchestrator.process_turn(user_input)
        
        self.assertEqual(result['router_decision']['tool'], 'search_inventory')
        self.assertEqual(result['router_decision']['args']['block_id'], 'A')

    def test_broad_availability_flow_stays_compact(self):
        """
        Broad availability requests should avoid sending many raw units to the LLM.
        """
        user_input = "Mevcut evler neler?"

        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "search_inventory", "args": {"status": "available"}}',
            "Şu anda uygun daireler var. İstersen tip ya da bütçeye göre daraltayım."
        ]

        result = self.orchestrator.process_turn(user_input)

        self.assertEqual(result['router_decision']['tool'], 'search_inventory')
        self.assertEqual(result['context']['units'], [])
        self.assertTrue(len(result['context']['next_questions']) > 0)

    def test_follow_up_price_keeps_previous_preference(self):
        """
        Follow-up price questions should preserve earlier property preferences.
        """
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "search_inventory", "args": {"sun_exposure": "high", "status": "available"}}',
            "Evet, güneş alan seçenekler var.",
            '{"tool": "search_inventory", "args": {"status": "available"}}',
            "Fiyat aralığını paylaşayım."
        ]

        self.orchestrator.process_turn("Güneş alan daireniz var mı?")
        result = self.orchestrator.process_turn("Fiyat bilgisi verebilir misin?")

        self.assertEqual(result['router_decision']['tool'], 'search_inventory')
        self.assertEqual(result['router_decision']['args']['sun_exposure'], 'high')

    def test_first_turn_prompt_marks_intro_stage(self):
        """
        The first reply should carry first-turn metadata so the model can introduce itself.
        """
        captured = []

        def fake_generate(system_prompt, user_prompt, json_mode=False):
            captured.append((system_prompt, user_prompt, json_mode))
            if json_mode:
                return '{"tool": "answer_general", "args": {"category": "other"}}'
            return "Memnuniyetle yardımcı olayım. Bütçenizi paylaşır mısınız?"

        self.orchestrator.llm_client.generate = fake_generate
        result = self.orchestrator.process_turn("ev almak istiyorum")

        self.assertIn("first_turn=True", captured[-1][0])
        self.assertIn("Bütçenizi", result["agent_response"])

    def test_first_greeting_is_generated_by_llm_with_profile_context(self):
        """
        Ilk selamlama artik sablon degil: LLM, SALES PROFILE ve first_turn
        baglamiyla uretir.
        """
        self.orchestrator.admin_store.get_sales_profile = MagicMock(
            return_value={"consultant_name": "Muhammed", "consultant_title": ""}
        )
        captured = []

        def fake_generate(system_prompt, user_prompt, json_mode=False):
            captured.append(system_prompt)
            if json_mode:
                return '{"tool": "answer_general", "args": {"category": "greeting"}}'
            return "Merhaba, ben Ekinciler Residence satış danışmanı Muhammed. Nasıl yardımcı olabilirim?"

        self.orchestrator.llm_client.generate = fake_generate

        result = self.orchestrator.process_turn("merhaba")

        self.assertEqual(
            result["agent_response"],
            "Merhaba, ben Ekinciler Residence satış danışmanı Muhammed. Nasıl yardımcı olabilirim?"
        )
        self.assertIn("Muhammed", captured[-1])
        self.assertIn("first_turn=True", captured[-1])

    def test_follow_up_response_drops_redundant_greeting(self):
        """
        Follow-up turns should not keep saying merhaba.
        """
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "answer_general", "args": {"category": "greeting"}}',
            "Merhaba, hoş geldiniz!",
            '{"tool": "answer_general", "args": {"category": "other"}}',
            "Merhaba, nasıl yardımcı olabilirim? Bütçeniz ve daire tipiniz nedir?"
        ]

        self.orchestrator.process_turn("selam")
        result = self.orchestrator.process_turn("ev bakıcam da")

        self.assertEqual(
            result["agent_response"],
            "Bütçeniz ve daire tipiniz nedir?"
        )

    def test_low_budget_context_includes_starting_price(self):
        """
        If nothing matches a low budget, context should include the starting price.
        """
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "search_inventory", "args": {"max_price": 3000000, "status": "available"}}',
            "Bu bütçede uygun daire yok."
        ]

        result = self.orchestrator.process_turn("Benim bütçem 3 milyon civarında")

        self.assertIn("Başlangıç fiyatımız 5 milyon 660 bin TL.", result["context"]["price_info"]["summary"])

    def test_currency_output_is_normalized(self):
        """
        Follow-up answers should normalize awkward currency abbreviations.
        """
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "answer_general", "args": {"category": "greeting"}}',
            "Merhaba, hoş geldiniz!",
            '{"tool": "search_inventory", "args": {"max_price": 30000000, "status": "available"}}',
            "26,98 Myr'e kadar dairelerimiz var."
        ]

        self.orchestrator.process_turn("selam")
        result = self.orchestrator.process_turn("30 milyon")

        self.assertEqual(result["agent_response"], "26,98 milyon TL kadar dairelerimiz var.")

    def test_project_overview_category_adds_context_fact(self):
        """
        Router project_overview kategorisi sectiginde LLM'e ozet baglami eklenir;
        cevap LLM'den gelir.
        """
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "answer_general", "args": {"category": "project_overview"}}',
            "Projemiz Ümraniye'de; isterseniz fiyat ve daire tiplerinden başlayalım."
        ]

        result = self.orchestrator.process_turn("Merhaba evleriniz hakkında bilgi almak istiyorum")

        self.assertEqual(result["router_decision"]["args"]["category"], "project_overview")
        self.assertIn(
            "Müşteri proje ve uygun daireler hakkında kısa bir özet istiyor.",
            result["context"]["message_facts"],
        )
        self.assertIn("Ümraniye", result["agent_response"])

    def test_empty_input_does_not_start_conversation(self):
        """
        Empty input should not poison history or consume the first-turn greeting.
        """
        result = self.orchestrator.process_turn("   ")

        self.assertEqual(result["agent_response"], "Sizi dinliyorum.")
        self.assertEqual(result["router_decision"]["args"]["category"], "empty_input")
        self.assertEqual(len(self.orchestrator.history), 0)

        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "answer_general", "args": {"category": "greeting"}}',
            "Merhaba, size nasıl yardımcı olabilirim?"
        ]
        follow_up = self.orchestrator.process_turn("selam")
        self.assertEqual(follow_up["agent_response"], "Merhaba, size nasıl yardımcı olabilirim?")
        self.assertEqual(len(self.orchestrator.history), 2)

    def test_history_is_hydrated_from_store_after_restart(self):
        """
        Surec yeniden basladiginda bellek ici gecmis bos kalir; DB'den geri
        yuklenmeli ki kalici akis oturumu silinmesin ve LLM baglami gorsun.
        """
        self.orchestrator.admin_store.get_latest_session_id_for_user = MagicMock(return_value="sess-1")
        self.orchestrator.admin_store.get_conversation = MagicMock(return_value={
            "session": {"session_id": "sess-1"},
            "turns": [
                {"user_input": "3+1 daire var mı?", "agent_response": "Evet, 3+1 seçeneklerimiz mevcut."},
            ],
        })

        history = self.orchestrator._get_conversation_history("veli", "whatsapp")

        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["role"], "user")
        self.assertEqual(history[0]["text"], "3+1 daire var mı?")
        self.assertIn("intents", history[0])
        self.assertEqual(history[0]["slots"].get("flat_type_id"), "FT-3P1")
        self.assertEqual(history[1]["role"], "agent")

    def test_history_hydration_returns_empty_for_new_user(self):
        history = self.orchestrator._get_conversation_history("yeni-kullanici", "whatsapp")
        self.assertEqual(history, [])

    def test_bare_number_in_free_text_is_not_treated_as_budget(self):
        """
        '5' gibi ciplak sayilar kat/kapi numarasi olabilir; serbest metinde
        butce olarak yorumlanmamali (eskiden 5 -> 5.000.000 TL oluyordu).
        """
        self.assertEqual(self.orchestrator._extract_budget_preferences("5"), {})
        self.assertEqual(self.orchestrator._extract_budget_preferences("50"), {})
        self.assertEqual(
            self.orchestrator._extract_budget_preferences("bütçem 8 milyon"),
            {"budget_max_try": 8_000_000},
        )

    def test_ai_notes_are_generated_for_user(self):
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "search_inventory", "args": {"flat_type_id": "FT-2P1", "max_price": 8000000, "status": "available"}}',
            "2+1 seçenekleri paylaşayım."
        ]

        self.orchestrator.process_turn("2+1 için 8 milyon bütçem var", user_id="alice")

        self.assertTrue(self.orchestrator.admin_store.save_user_ai_notes.called)
        kwargs = self.orchestrator.admin_store.save_user_ai_notes.call_args.kwargs
        self.assertEqual(kwargs["user_id"], "alice")
        self.assertIn("2+1", kwargs["ai_summary"])
        self.assertEqual(kwargs["ai_notes"]["preferred_flat_type"], "2+1")
        self.assertEqual(kwargs["ai_notes"]["budget_max_try"], 8000000)

    def test_selected_listing_is_saved_into_ai_notes(self):
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "search_inventory", "args": {"status": "available", "sort_by": "price_desc"}}',
            "En pahalı dairemiz 26 milyon 980 bin TL."
        ]

        self.orchestrator.process_turn("en pahallisi", user_id="alice")

        kwargs = self.orchestrator.admin_store.save_user_ai_notes.call_args.kwargs
        self.assertEqual(kwargs["ai_notes"]["selected_listing_id"], "INV-0056")

    def test_ai_summary_does_not_expose_internal_handoff_language(self):
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "trigger_handoff", "args": {"reason": "Visit request", "share_contact_details": true, "share_location": true}}',
            "Sizi satış danışmanımıza yönlendiriyorum, konumu da iletiyorum."
        ]

        self.orchestrator.process_turn("ofise gelmek istiyorum", user_id="alice", channel="whatsapp")

        kwargs = self.orchestrator.admin_store.save_user_ai_notes.call_args.kwargs
        self.assertNotIn("handoff gerekli", kwargs["ai_summary"].lower())
        self.assertNotIn("selected listing", kwargs["ai_summary"].lower())

    def test_router_receives_conversation_context_for_referential_follow_up(self):
        captured = []

        def fake_generate(system_prompt, user_prompt, json_mode=False):
            captured.append((system_prompt, user_prompt, json_mode))
            if json_mode and len(captured) == 1:
                return '{"tool": "search_inventory", "args": {"status": "available", "sort_by": "price_desc"}}'
            if json_mode:
                return '{"tool": "answer_general", "args": {"category": "listing_overview"}}'
            return "Tamam."

        self.orchestrator.llm_client.generate = fake_generate
        self.orchestrator.admin_store.log_turn = MagicMock()
        self.orchestrator.admin_store.save_user_ai_notes = MagicMock()

        self.orchestrator.process_turn("en pahallisi", user_id="alice")
        self.orchestrator.process_turn("bu evi anlat bana", user_id="alice")

        router_prompt_for_second_turn = captured[2][1]
        self.assertIn("RECENT CONVERSATION", router_prompt_for_second_turn)
        self.assertIn("Current listing: INV-0056", router_prompt_for_second_turn)
        self.assertIn("en pahallisi", router_prompt_for_second_turn)

    def test_final_generation_receives_recent_history_for_qualification_follow_up(self):
        self.orchestrator.llm_client.generate = MagicMock(side_effect=[
            '{"tool": "answer_general", "args": {"category": "qualification"}}',
            "Bütçeniz ve daire tipi tercihiniz nedir?",
            '{"tool": "answer_general", "args": {"category": "qualification"}}',
            "Ümraniye, İstanbul’daki dairelerimiz var; metrekare, kat ya da yön gibi tercih ettiğiniz bir özellik var mı?",
            '{"tool": "answer_general", "args": {"category": "qualification"}}',
            "Tamam, ilerleyelim."
        ])

        self.orchestrator.process_turn("Ev almak istiyorum", user_id="alice")
        self.orchestrator.process_turn("Para sorunum yok, önemli olan İstanbul'da olması", user_id="alice")
        self.orchestrator.process_turn("Hayır fark etmez", user_id="alice")

        final_call = self.orchestrator.llm_client.generate.call_args_list[-1]
        final_system_prompt = final_call.kwargs["system_prompt"]
        final_user_prompt = final_call.kwargs["user_prompt"]

        self.assertIn("RECENT CONVERSATION", final_user_prompt)
        self.assertIn("Para sorunum yok", final_user_prompt)
        self.assertIn("metrekare, kat ya da yön", final_user_prompt)
        self.assertIn("Conversation focus: qualification", final_system_prompt)

    def test_qualification_category_sets_context_instead_of_general_greeting(self):
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "answer_general", "args": {"category": "qualification"}}',
            "Tamam."
        ]

        result = self.orchestrator.process_turn("Hayır fark etmez", user_id="alice")

        self.assertIn("Müşteri ihtiyaç analizi akışında.", result["context"]["message_facts"])
        self.assertEqual(self.orchestrator.user_profiles["alice"]["conversation_focus"], "qualification")

    def test_listing_reference_injects_selected_listing_details_into_context(self):
        """
        Secili ilan varken router listing_overview derse ilanin verileri LLM
        baglamina eklenmeli; cevap LLM'den gelir.
        """
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "search_inventory", "args": {"status": "available", "sort_by": "price_desc"}}',
            "En pahalı dairemiz 26 milyon 980 bin TL.",
            '{"tool": "answer_general", "args": {"category": "listing_overview"}}',
            "Bu daire 5+1 dubleks, 26 milyon 980 bin TL; dilerseniz detaylandırayım."
        ]

        self.orchestrator.process_turn("en pahallisi", user_id="alice")
        result = self.orchestrator.process_turn("bu evi anlat bana", user_id="alice")

        self.assertEqual(result["router_decision"]["args"]["category"], "listing_overview")
        listing_facts = [fact for fact in result["context"]["message_facts"] if fact.startswith("Seçili daire")]
        self.assertEqual(len(listing_facts), 1)
        self.assertIn("5+1 Duplex", listing_facts[0])
        self.assertIn("26 milyon 980 bin TL", listing_facts[0])

    def test_handoff_prompt_receives_sales_profile_context(self):
        """
        Handoff cevabi artik LLM'den gelir; danisman bilgileri SALES PROFILE
        olarak prompt'a girer, sablon yoktur.
        """
        self.orchestrator.admin_store.get_sales_profile = MagicMock(return_value={
            "consultant_name": "Ayse Yilmaz",
            "consultant_title": "satis danismani",
            "phone_number": "+905551112233",
            "whatsapp_number": "+905551112233",
            "office_name": "Ekinciler Residence Satis Ofisi",
            "office_address": "Umraniye, Istanbul",
            "maps_url": "https://maps.example/ofis",
            "latitude": "41.015",
            "longitude": "29.123",
            "location_label": "Ekinciler Residence",
            "auto_share_whatsapp_location": True,
            "updated_at": "2026-04-03T10:00:00+00:00",
        })
        captured = []

        def fake_generate(system_prompt, user_prompt, json_mode=False):
            captured.append(system_prompt)
            if json_mode:
                return '{"tool": "trigger_handoff", "args": {"reason": "Visit request", "share_contact_details": true, "share_location": true}}'
            return "Sizi danışmanımız Ayse Yilmaz'a yönlendiriyorum; konumu ve numarayı iletiyorum."

        self.orchestrator.llm_client.generate = fake_generate

        result = self.orchestrator.process_turn("beni yonlendir", user_id="alice", channel="whatsapp")

        self.assertTrue(result["context"]["handoff"]["required"])
        self.assertTrue(result["context"]["handoff"]["share_location"])
        self.assertIn("Ayse Yilmaz", captured[-1])
        self.assertIn("+905551112233", captured[-1])
        self.assertIn("Ayse Yilmaz", result["agent_response"])

    def test_contact_reassurance_question_does_not_trigger_handoff(self):
        self.orchestrator.admin_store.get_sales_profile = MagicMock(return_value={
            "consultant_name": "Baha Özsoy",
            "consultant_title": "satış asistanı",
            "phone_number": "+905551112233",
            "whatsapp_number": "+905551112233",
            "office_name": "Ekinciler Residence Satış Ofisi",
            "office_address": "Ümraniye, İstanbul",
            "maps_url": "",
            "latitude": "",
            "longitude": "",
            "location_label": "",
            "auto_share_whatsapp_location": True,
            "updated_at": "2026-04-03T10:00:00+00:00",
        })
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "answer_general", "args": {"category": "contact_reassurance"}}',
            "Evet, satış danışmanımız telefonla yardımcı olur; isterseniz numarayı paylaşayım."
        ]

        result = self.orchestrator.process_turn("Ararsam da cevap veriyor mu", user_id="alice", channel="whatsapp")

        self.assertEqual(result["router_decision"]["tool"], "answer_general")
        self.assertFalse(result["context"]["handoff"]["required"])
        self.assertIn("yardımcı olur", result["agent_response"])

    def test_router_driven_location_share_sets_handoff_flags(self):
        """
        Konum/iletisim paylasimini artik keyword kisayolu degil router LLM karari
        belirler; bayraklar handoff baglamina islenir.
        """
        self.orchestrator.admin_store.get_sales_profile = MagicMock(return_value={
            "consultant_name": "Baha Özsoy",
            "consultant_title": "satış asistanı",
            "phone_number": "+905551112233",
            "whatsapp_number": "+905551112233",
            "office_name": "Ekinciler Residence Satış Ofisi",
            "office_address": "Ümraniye, İstanbul",
            "maps_url": "https://maps.example/ofis",
            "latitude": "41.015",
            "longitude": "29.123",
            "location_label": "Ekinciler Residence",
            "auto_share_whatsapp_location": True,
            "updated_at": "2026-04-03T10:00:00+00:00",
        })
        self.orchestrator.llm_client.generate.side_effect = [
            '{"tool": "trigger_handoff", "args": {"reason": "Office location request", "share_contact_details": true, "share_location": true}}',
            "Ofisimizin konumunu ve danışmanımızın iletişim bilgilerini paylaşıyorum."
        ]

        result = self.orchestrator.process_turn("ofisiniz nerde", user_id="alice", channel="whatsapp")

        self.assertEqual(result["router_decision"]["tool"], "trigger_handoff")
        self.assertTrue(result["context"]["handoff"]["required"])
        self.assertTrue(result["context"]["handoff"]["share_location"])
        self.assertTrue(result["context"]["handoff"]["share_contact_details"])

if __name__ == '__main__':
    unittest.main()
