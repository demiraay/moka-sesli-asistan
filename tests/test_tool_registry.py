"""Arac kayit defteri: sema uretimi, tip duzeltme, panel etiketleri, token butcesi."""

import json
import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import tools
from core.tools.registry import MAX_DESCRIPTION_CHARS, PURE, SIDE_EFFECT, TERMINAL

EXPECTED_TOOLS = {
    "get_settlement_status", "find_transaction", "troubleshoot_pos", "explain_fees",
    "send_statement", "create_payment_link", "recommend_offer", "trigger_handoff",
    "answer_general", "update_customer_card", "find_dormant_merchants",
    "record_crm_note",
}


class TestRegistryContents(unittest.TestCase):
    def test_all_nine_tools_registered(self):
        self.assertEqual(set(tools.REGISTRY), EXPECTED_TOOLS)

    def test_every_tool_has_a_panel_label(self):
        """Eski elle yazilmis tool_map'te 'answer_general' eksikti."""
        labels = tools.panel_tool_labels()
        self.assertEqual(set(labels), EXPECTED_TOOLS)
        for name, label in labels.items():
            self.assertTrue(label.strip(), f"{name} etiketsiz")

    def test_side_effect_classification(self):
        kinds = {name: spec.kind for name, spec in tools.REGISTRY.items()}
        self.assertEqual(kinds["trigger_handoff"], TERMINAL)
        for name in ("send_statement", "create_payment_link", "recommend_offer",
                     "troubleshoot_pos"):
            self.assertEqual(kinds[name], SIDE_EFFECT, name)
        for name in ("get_settlement_status", "find_transaction", "answer_general",
                     "update_customer_card", "find_dormant_merchants"):
            self.assertEqual(kinds[name], PURE, name)

    def test_once_per_call_guards_on_irreversible_tools(self):
        self.assertTrue(tools.REGISTRY["send_statement"].once_per_call)
        self.assertTrue(tools.REGISTRY["trigger_handoff"].once_per_call)

    def test_tools_not_needing_a_merchant(self):
        self.assertFalse(tools.REGISTRY["answer_general"].requires_merchant)
        self.assertFalse(tools.REGISTRY["trigger_handoff"].requires_merchant)
        self.assertFalse(tools.REGISTRY["find_dormant_merchants"].requires_merchant)
        self.assertTrue(tools.REGISTRY["get_settlement_status"].requires_merchant)


class TestSchemaGeneration(unittest.TestCase):
    def setUp(self):
        self.schema = tools.openai_tools_schema()

    def test_modern_function_wrapper(self):
        """Eski sema duz {name, description, parameters} idi; API sarmal bekler."""
        for entry in self.schema:
            self.assertEqual(entry["type"], "function")
            self.assertIn("function", entry)
            for field in ("name", "description", "parameters"):
                self.assertIn(field, entry["function"])

    def test_handoff_exposes_share_contact_details(self):
        """Eski semada YOKTU ama kod ve panel kuyrugu bu alani kullaniyordu."""
        handoff = next(e["function"] for e in self.schema
                       if e["function"]["name"] == "trigger_handoff")
        self.assertIn("share_contact_details", handoff["parameters"]["properties"])

    def test_parameters_are_valid_json_schema_objects(self):
        for entry in self.schema:
            params = entry["function"]["parameters"]
            self.assertEqual(params.get("type"), "object")
            self.assertIsInstance(params.get("properties", {}), dict)
            for required in params.get("required", []):
                self.assertIn(required, params["properties"],
                              f"{entry['function']['name']}: required alan sema disinda")

    def test_description_budget(self):
        """Token butcesi kilidi: sema HER iterasyonda gider.

        Eski tam sema ~2782 token tutuyordu ve Groq free tier TPM limitini tek
        turda yiyordu; bu yuzden ayri bir 'compact' rehber yazilmisti.
        """
        for entry in self.schema:
            description = entry["function"]["description"]
            self.assertLessEqual(len(description), MAX_DESCRIPTION_CHARS,
                                 f"{entry['function']['name']} aciklamasi cok uzun")
        # 12 arac icin ~5.5K karakter (~1.4K token). Yeni arac eklendikce bu
        # tavan bilincli olarak yukseltilir; amac kacak uzun aciklamalari
        # yakalamak, arac sayisini dondurmak degil.
        total = len(json.dumps(self.schema, ensure_ascii=False))
        self.assertLess(total, 6500, f"toplam sema cok buyudu: {total} karakter")

    def test_tool_guide_derives_from_registry(self):
        guide = tools.tool_guide()
        for name in EXPECTED_TOOLS:
            self.assertIn(name, guide)

    def test_router_prompt_contains_every_tool(self):
        prompt = tools.build_router_system_prompt()
        for name in EXPECTED_TOOLS:
            self.assertIn(name, prompt)


class TestCoerceArgs(unittest.TestCase):
    def setUp(self):
        self.transaction = tools.REGISTRY["find_transaction"]
        self.handoff = tools.REGISTRY["trigger_handoff"]

    def test_numeric_string_becomes_float(self):
        result = tools.coerce_args(self.transaction, {"amount_try": "1250"})
        self.assertEqual(result["amount_try"], 1250.0)

    def test_thousands_separator_is_understood(self):
        """'1.250' 1.25 DEGIL 1250 olmali."""
        result = tools.coerce_args(self.transaction, {"amount_try": "1.250"})
        self.assertEqual(result["amount_try"], 1250.0)

    def test_unparseable_number_key_is_dropped(self):
        """Anahtar None ile birakilirsa handler 'deger verildi ama bos' sanir."""
        result = tools.coerce_args(self.transaction, {"amount_try": "bin iki yüz"})
        self.assertNotIn("amount_try", result)

    def test_boolean_never_becomes_an_amount(self):
        """bool, int'in alt sinifidir: True 1.0 TL olmamali."""
        result = tools.coerce_args(self.transaction, {"amount_try": True})
        self.assertNotIn("amount_try", result)

    def test_boolean_strings_are_coerced(self):
        result = tools.coerce_args(self.handoff,
                                   {"reason": "test", "share_contact_details": "true"})
        self.assertIs(result["share_contact_details"], True)

    def test_array_field_wraps_scalar(self):
        result = tools.coerce_args(self.handoff,
                                   {"reason": "test", "missing_info": "tutar"})
        self.assertEqual(result["missing_info"], ["tutar"])

    def test_unknown_keys_pass_through(self):
        result = tools.coerce_args(self.transaction, {"beklenmeyen": "x"})
        self.assertEqual(result["beklenmeyen"], "x")

    def test_non_dict_args_become_empty(self):
        self.assertEqual(tools.coerce_args(self.transaction, None), {})


class TestDuplicateRegistration(unittest.TestCase):
    def test_duplicate_name_raises(self):
        with self.assertRaises(ValueError):
            @tools.tool(name="find_transaction", description="x",
                        parameters={"type": "object", "properties": {}})
            def _duplicate(ctx, args):
                return ""

    def test_overlong_description_raises(self):
        with self.assertRaises(ValueError):
            @tools.tool(name="cok_uzun_aciklama", description="x" * (MAX_DESCRIPTION_CHARS + 1),
                        parameters={"type": "object", "properties": {}})
            def _verbose(ctx, args):
                return ""


if __name__ == "__main__":
    unittest.main()


class TestPlannerPromptGuardrails(unittest.TestCase):
    """Planlayici prompt'undaki DAVRANIS kurallari.

    Bu kurallarin ETKISI canli modelle dogrulanir (birim testi LLM davranisini
    olcemez); buradaki testler kurallarin yanlislikla SILINMESINE karsidir.
    Gecmiste eksikligi gercek bir hataya yol acti: musteri "ekstreyi nasil
    yollayacaksin?" diye SORDU, ajan send_statement calistirip GONDERDI.
    """

    def setUp(self):
        self.prompt = tools.build_planner_system_prompt()

    def test_question_versus_request_distinction_exists(self):
        lowered = self.prompt.lower()
        self.assertIn("soru mu, talep mi", lowered)
        for hint in ("nasil", "answer_general"):
            self.assertIn(hint, lowered)

    def test_action_tools_are_named_as_forbidden_for_questions(self):
        self.assertIn("send_statement", self.prompt)
        self.assertIn("create_payment_link", self.prompt)

    def test_explicit_consent_words_are_listed(self):
        for word in ("gonder", "evet", "olur"):
            self.assertIn(word, self.prompt.lower())

    def test_diagnose_before_acting_rule_exists(self):
        self.assertIn("TESHIS ONCE", self.prompt)


class TestComposerPromptGuardrails(unittest.TestCase):
    def setUp(self):
        from core.prompts import SystemPromptBuilder
        self.prompt = SystemPromptBuilder().build_system_prompt()

    def test_answer_the_question_rule_exists(self):
        self.assertIn("ANSWER THE QUESTION THAT WAS ASKED", self.prompt)
        self.assertIn("Never answer a question with an action", self.prompt)

    def test_pacing_rule_exists(self):
        self.assertIn("CONVERSATIONAL PACING", self.prompt)
        self.assertIn("One step per turn", self.prompt)

    def test_no_unverified_action_claims_rule_exists(self):
        self.assertIn("NEVER claim an ACTION", self.prompt)
