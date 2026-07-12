import sys
import os
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.prompts import SystemPromptBuilder


class TestSystemPromptBuilder(unittest.TestCase):
    def test_prompt_contains_voice_rules(self):
        prompt = SystemPromptBuilder().build_system_prompt()

        self.assertIn("VOICE AGENT", prompt)
        self.assertIn("Default to 1 or 2 short sentences.", prompt)
        self.assertIn("Never use tables, markdown, bullet lists, headings, or long formatted outputs.", prompt)
        self.assertIn("Behave like a skilled real estate consultant", prompt)
        self.assertIn("NEVER invent, guess, or alter a phone number", prompt)
        self.assertIn("share_contact_details=true", prompt)
        self.assertIn("do not route them to the office immediately", prompt)
        self.assertIn("briefly introduce yourself", prompt)
        self.assertIn('Do not say "Merhaba", "Selam", or another welcome phrase again', prompt)
        self.assertIn('Never use abbreviations like "TRY", "Myr"', prompt)


if __name__ == '__main__':
    unittest.main()
