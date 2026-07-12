import sys
import os
import unittest

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.rules import RuleEngine

class TestRuleEngine(unittest.TestCase):
    def setUp(self):
        self.engine = RuleEngine()

    def test_policies(self):
        policies = self.engine.get_policies()
        # Based on rules.json viewed earlier
        self.assertFalse(policies['allow_negotiation'])
        self.assertFalse(policies['custom_discount'])
        self.assertTrue(policies['require_stock_check'])

if __name__ == '__main__':
    unittest.main()
