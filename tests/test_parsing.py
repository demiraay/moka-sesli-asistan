import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.parsing import extract_try_amount, parse_llm_json


class TestExtractTryAmount(unittest.TestCase):
    def test_million_expression(self):
        self.assertEqual(extract_try_amount("bütçem 8 milyon"), 8_000_000)

    def test_million_with_decimal_comma(self):
        self.assertEqual(extract_try_amount("8,5 milyon olur"), 8_500_000)

    def test_thousand_expression(self):
        self.assertEqual(extract_try_amount("750 bin civarı"), 750_000)

    def test_explicit_tl_amount(self):
        self.assertEqual(extract_try_amount("8500000 tl"), 8_500_000)

    def test_bare_number_is_ambiguous_by_default(self):
        # '5' kat cevabi ya da adet olabilir; bayrak olmadan butce sayilmamali.
        self.assertIsNone(extract_try_amount("5"))
        self.assertIsNone(extract_try_amount("50"))

    def test_bare_number_as_budget_answer(self):
        self.assertEqual(extract_try_amount("50", assume_bare_number_is_budget=True), 50_000_000)
        self.assertEqual(extract_try_amount("8,5", assume_bare_number_is_budget=True), 8_500_000)

    def test_bare_number_above_100_is_taken_as_is(self):
        self.assertEqual(extract_try_amount("150", assume_bare_number_is_budget=True), 150)

    def test_no_amount(self):
        self.assertIsNone(extract_try_amount("güney cephe olsun"))


class TestParseLlmJson(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(parse_llm_json('{"tool": "check_price"}'), {"tool": "check_price"})

    def test_code_fenced_json(self):
        raw = '```json\n{"tool": "search_inventory", "args": {}}\n```'
        self.assertEqual(parse_llm_json(raw), {"tool": "search_inventory", "args": {}})

    def test_single_quoted_fallback(self):
        self.assertEqual(parse_llm_json("{'tool': 'answer_general'}"), {"tool": "answer_general"})

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            parse_llm_json('[1, 2, 3]')


if __name__ == "__main__":
    unittest.main()
