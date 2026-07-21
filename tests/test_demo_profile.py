"""Gosterim profili: panelden duzenlenen isletme + turetilen tutarli veri."""

import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import demo_profile
from core.repository import MerchantRepository

BASE_DIR = Path(__file__).resolve().parent.parent
SOURCE_DB = BASE_DIR / "data" / "moka.sqlite3"


class DemoProfileTestCase(unittest.TestCase):
    """Her test kendi DB kopyasinda calisir; gercek demo verisi kirlenmesin."""

    @classmethod
    def setUpClass(cls):
        if not SOURCE_DB.exists():
            raise unittest.SkipTest("moka.sqlite3 yok; once seed_demo_data.py calistirin")

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = Path(self.temp_dir.name) / "moka.sqlite3"
        shutil.copy(SOURCE_DB, self.db_path)
        self.repo = MerchantRepository(self.db_path)
        # Kaynak DB'de profil zaten olusturulmus olabilir; testler kendi
        # baslangic durumunu kursun.
        self._remove_test_merchant()

    def _remove_test_merchant(self):
        from core import db
        with db.session(self.db_path) as connection:
            for table in ("transactions", "settlements", "pos_devices",
                          "merchant_products", "merchant_monthly_volume"):
                connection.execute(f"DELETE FROM {table} WHERE merchant_id = ?",
                                   (demo_profile.TEST_MERCHANT_ID,))
            connection.execute("DELETE FROM identities WHERE merchant_id = ?",
                               (demo_profile.TEST_MERCHANT_ID,))
            connection.execute("DELETE FROM merchants WHERE merchant_id = ?",
                               (demo_profile.TEST_MERCHANT_ID,))


class TestProfileLifecycle(DemoProfileTestCase):
    def test_ensure_exists_creates_then_is_idempotent(self):
        self.assertTrue(demo_profile.ensure_exists(self.repo))
        self.assertFalse(demo_profile.ensure_exists(self.repo))
        merchant = self.repo.get_merchant(demo_profile.TEST_MERCHANT_ID)
        self.assertEqual(merchant["business_name"],
                         demo_profile.DEFAULT_PROFILE["business_name"])

    def test_saved_fields_round_trip(self):
        demo_profile.save_profile(self.repo, {
            "business_name": "Ekinci Kahve", "owner_name": "Muhammed Ekinci",
            "salutation": "Muhammed Bey", "sector": "Kafe", "city": "Ankara",
            "district": "Çankaya", "phone": "+905551234567", "email": "m@ekinci.com",
            "iban_masked": "TR** **** **12 34", "commission_plan_id": "PLAN-PLUS",
            "device_model": "Moka P30 Pro", "device_status": "pasif",
            "device_note": "3 gündür bağlantı yok",
            "products": ["sanal_pos"], "volumes": [10, 20, 30, 40, 50, 60],
        })
        profile = demo_profile.read_profile(self.repo)
        self.assertEqual(profile["business_name"], "Ekinci Kahve")
        self.assertEqual(profile["salutation"], "Muhammed Bey")
        self.assertEqual(profile["commission_plan_id"], "PLAN-PLUS")
        self.assertEqual(profile["products"], ["sanal_pos"])
        self.assertEqual(profile["device_status"], "pasif")
        self.assertEqual(profile["device_note"], "3 gündür bağlantı yok")
        self.assertEqual(profile["volumes"], [10, 20, 30, 40, 50, 60])

    def test_saving_twice_does_not_duplicate_rows(self):
        """Turetilmis veri her kayitta SIFIRDAN uretilir, birikmez."""
        demo_profile.ensure_exists(self.repo)
        first = len(self.repo.find_transactions(demo_profile.TEST_MERCHANT_ID, limit=999))
        demo_profile.save_profile(self.repo, demo_profile.read_profile(self.repo))
        second = len(self.repo.find_transactions(demo_profile.TEST_MERCHANT_ID, limit=999))
        self.assertEqual(first, second)
        self.assertEqual(
            len(self.repo.list_merchants()),
            len({m["merchant_id"] for m in self.repo.list_merchants()}))

    def test_other_merchants_are_untouched(self):
        before = self.repo.get_merchant("M-1001")
        demo_profile.save_profile(self.repo, {"business_name": "X",
                                              "volumes": [1, 2, 3, 4, 5, 6]})
        after = self.repo.get_merchant("M-1001")
        self.assertEqual(before["business_name"], after["business_name"])
        self.assertEqual(len(self.repo.find_transactions("M-1001", limit=999)),
                         len(self.repo.find_transactions("M-1001", limit=999)))


class TestGeneratedDataCoherence(DemoProfileTestCase):
    """Asistanin soyleyecegi rakamlar birbirini tutmali."""

    def setUp(self):
        super().setUp()
        demo_profile.save_profile(self.repo, {
            **demo_profile.DEFAULT_PROFILE,
            "commission_plan_id": "PLAN-STD",
            "volumes": [90000, 95000, 100000, 110000, 120000, 150000],
        })
        self.merchant_id = demo_profile.TEST_MERCHANT_ID

    def test_settlement_totals_match_their_transactions(self):
        for settlement in self.repo.list_settlements(self.merchant_id, limit=99):
            rows = self.repo._query(
                "SELECT amount_try FROM transactions WHERE settlement_batch_id = ?",
                (settlement["batch_id"],))
            total = round(sum(row["amount_try"] for row in rows), 2)
            self.assertAlmostEqual(total, settlement["gross_try"], places=1,
                                   msg=f"{settlement['batch_id']} brut != islemler toplami")

    def test_net_equals_gross_minus_commission(self):
        for settlement in self.repo.list_settlements(self.merchant_id, limit=99):
            self.assertAlmostEqual(
                settlement["net_try"],
                round(settlement["gross_try"] - settlement["commission_try"], 2),
                places=1)

    def test_commission_follows_the_plan_rate(self):
        rate = self.repo.get_plan("PLAN-STD")["rate_pct"]
        for settlement in self.repo.list_settlements(self.merchant_id, limit=99):
            expected = round(settlement["gross_try"] * rate / 100, 2)
            self.assertAlmostEqual(settlement["commission_try"], expected, places=1)

    def test_there_is_always_a_pending_payout(self):
        """'Param ne zaman yatacak' senaryosu icin planlanmis hakedis sart."""
        statuses = [s["status"] for s in self.repo.list_settlements(self.merchant_id, limit=99)]
        self.assertIn("planlandı", statuses)

    def test_transactions_are_searchable_by_amount_and_day(self):
        rows = self.repo.find_transactions(self.merchant_id, limit=1)
        self.assertTrue(rows)
        txn = rows[0]
        found = self.repo.find_transactions(
            self.merchant_id, amount_try=txn["amount_try"],
            on_date=txn["timestamp"][:10])
        self.assertTrue(found, "uretilen islem kendi tutar/tarihiyle bulunamadi")

    def test_dates_are_within_the_last_week(self):
        today = date.today()
        for settlement in self.repo.list_settlements(self.merchant_id, limit=99):
            age = (today - date.fromisoformat(settlement["batch_date"])).days
            self.assertGreaterEqual(age, 0)
            self.assertLessEqual(age, 7, "hakedis tarihi demo icin fazla eski")

    def test_generation_is_deterministic(self):
        first = [t["amount_try"] for t in
                 self.repo.find_transactions(self.merchant_id, limit=999)]
        demo_profile.save_profile(self.repo, demo_profile.read_profile(self.repo))
        second = [t["amount_try"] for t in
                  self.repo.find_transactions(self.merchant_id, limit=999)]
        self.assertEqual(first, second, "ayni profil ayni veriyi vermeli")


class TestDataDrivenVariety(DemoProfileTestCase):
    """Sabitler yerine profile ozgu cesitlilik; determinizm korunur."""

    def test_different_profiles_produce_different_data(self):
        """Tohum profile ozgu: farkli isletme -> farkli islem uretimi."""
        demo_profile.save_profile(self.repo, {**demo_profile.DEFAULT_PROFILE,
            "business_name": "Alfa Kafe", "phone": "+905550000011"})
        first = [t["amount_try"] for t in
                 self.repo.find_transactions(demo_profile.TEST_MERCHANT_ID, limit=999)]

        demo_profile.save_profile(self.repo, {**demo_profile.DEFAULT_PROFILE,
            "business_name": "Beta Market", "phone": "+905550000022"})
        second = [t["amount_try"] for t in
                  self.repo.find_transactions(demo_profile.TEST_MERCHANT_ID, limit=999)]

        # Farkli profiller ayni sabit veriyi vermemeli (adet ya da tutar farkli)
        self.assertNotEqual(first, second)

    def test_channel_variety_follows_products(self):
        """Sabit 'pos' degil: urun karmasi kanallara yansir."""
        demo_profile.save_profile(self.repo, {**demo_profile.DEFAULT_PROFILE,
            "business_name": "Çok Kanallı", "phone": "+905550000033",
            "products": ["fiziksel_pos", "sanal_pos", "odeme_linki"]})
        channels = {t["channel"] for t in
                    self.repo.find_transactions(demo_profile.TEST_MERCHANT_ID, limit=999)}
        self.assertLessEqual(channels, {"pos", "sanal_pos", "odeme_linki"})
        self.assertGreaterEqual(len(channels), 2)   # tek kanal degil

    def test_settlement_day_count_within_bounds(self):
        demo_profile.save_profile(self.repo, {**demo_profile.DEFAULT_PROFILE,
            "business_name": "Gama Fırın", "phone": "+905550000044"})
        batches = self.repo.list_settlements(demo_profile.TEST_MERCHANT_ID, limit=99)
        self.assertGreaterEqual(len(batches), 4)
        self.assertLessEqual(len(batches), 7)

    def test_payout_hours_are_not_all_ten(self):
        """Odeme saati sabit 10:00 degil: en az bir parti farkli saatte."""
        demo_profile.save_profile(self.repo, {**demo_profile.DEFAULT_PROFILE,
            "business_name": "Delta Pastane", "phone": "+905550000055",
            "volumes": [90000, 95000, 100000, 110000, 120000, 150000]})
        hours = {(s["payout_eta"] or "")[11:13]
                 for s in self.repo.list_settlements(demo_profile.TEST_MERCHANT_ID, limit=99)}
        self.assertTrue(any(h != "10" for h in hours if h))


class TestDormantScenario(DemoProfileTestCase):
    """Ciro dusurulerek geri kazanim senaryosu tetiklenebilmeli."""

    def test_low_last_month_makes_the_profile_dormant(self):
        demo_profile.save_profile(self.repo, {
            **demo_profile.DEFAULT_PROFILE,
            "volumes": [120000, 130000, 140000, 135000, 130000, 5000],
        })
        dormant_ids = {m["merchant_id"] for m in self.repo.list_dormant_merchants()}
        self.assertIn(demo_profile.TEST_MERCHANT_ID, dormant_ids)

    def test_healthy_growth_is_not_dormant(self):
        demo_profile.save_profile(self.repo, {
            **demo_profile.DEFAULT_PROFILE,
            "volumes": [90000, 100000, 110000, 120000, 130000, 145000],
        })
        dormant_ids = {m["merchant_id"] for m in self.repo.list_dormant_merchants()}
        self.assertNotIn(demo_profile.TEST_MERCHANT_ID, dormant_ids)


class TestInputHardening(DemoProfileTestCase):
    def test_unknown_plan_falls_back_to_standard(self):
        demo_profile.save_profile(self.repo, {**demo_profile.DEFAULT_PROFILE,
                                              "commission_plan_id": "PLAN-YOK"})
        self.assertEqual(
            demo_profile.read_profile(self.repo)["commission_plan_id"], "PLAN-STD")

    def test_garbage_volumes_fall_back(self):
        demo_profile.save_profile(self.repo, {**demo_profile.DEFAULT_PROFILE,
                                              "volumes": ["abc", "", "12.000", -5]})
        volumes = demo_profile.read_profile(self.repo)["volumes"]
        self.assertEqual(len(volumes), 6)
        self.assertTrue(all(isinstance(v, int) and v >= 0 for v in volumes))
        self.assertEqual(volumes[2], 12000)     # "12.000" binlik ayrac

    def test_empty_names_fall_back_to_defaults(self):
        demo_profile.save_profile(self.repo, {"business_name": "  ", "owner_name": ""})
        profile = demo_profile.read_profile(self.repo)
        self.assertTrue(profile["business_name"].strip())
        self.assertTrue(profile["owner_name"].strip())

    def test_invalid_products_are_rejected(self):
        demo_profile.save_profile(self.repo, {**demo_profile.DEFAULT_PROFILE,
                                              "products": ["hacked", "sanal_pos"]})
        self.assertEqual(demo_profile.read_profile(self.repo)["products"], ["sanal_pos"])

    def test_phone_is_linked_as_an_identity(self):
        """WhatsApp'tan bu numarayla yazilinca profil taninsin."""
        demo_profile.save_profile(self.repo, {**demo_profile.DEFAULT_PROFILE,
                                              "phone": "0555 111 22 33"})
        merchant = self.repo.find_merchant_by_phone("+905551112233")
        self.assertIsNotNone(merchant)
        self.assertEqual(merchant["merchant_id"], demo_profile.TEST_MERCHANT_ID)


if __name__ == "__main__":
    unittest.main()
