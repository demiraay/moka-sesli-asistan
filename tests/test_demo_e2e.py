import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.demo_e2e import _normalize_demo_user_id


class TestDemoE2E(unittest.TestCase):
    def test_normalize_demo_user_id_for_phone_number(self):
        self.assertEqual(_normalize_demo_user_id(" +90 555 111 22 33 "), "+905551112233")

    def test_normalize_demo_user_id_for_whatsapp_prefix(self):
        self.assertEqual(_normalize_demo_user_id("whatsapp:+90 555 111 22 33"), "+905551112233")

    def test_normalize_demo_user_id_for_local_zero_prefixed_phone_number(self):
        self.assertEqual(_normalize_demo_user_id("0540 111 22 33"), "+905401112233")

    def test_normalize_demo_user_id_for_short_local_prefix(self):
        self.assertEqual(_normalize_demo_user_id("0540"), "+90540")
        self.assertEqual(_normalize_demo_user_id("540"), "+90540")
        self.assertEqual(_normalize_demo_user_id("+90 540"), "+90540")

    def test_normalize_demo_user_id_for_plain_user_id(self):
        self.assertEqual(_normalize_demo_user_id("alice"), "alice")

    def test_normalize_demo_user_id_for_empty_value(self):
        self.assertEqual(_normalize_demo_user_id("   "), "default_user")


if __name__ == "__main__":
    unittest.main()
