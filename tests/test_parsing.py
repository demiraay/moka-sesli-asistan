import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.parsing import parse_llm_json


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
