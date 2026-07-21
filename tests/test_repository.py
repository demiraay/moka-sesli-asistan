"""MerchantRepository: SQL katmani, index kullanimi, tarih tazeleme, seed butunlugu."""

import json
import os
import subprocess
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db
from core.repository import MerchantRepository, normalize_day, tr_lower

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "moka.sqlite3"


class TestSeedIntegrity(unittest.TestCase):
    """Seed edilmis veritabaninin butunlugu."""

    @classmethod
    def setUpClass(cls):
        if not DB_PATH.exists():
            raise unittest.SkipTest("moka.sqlite3 yok; once seed_demo_data.py calistirin")
        cls.connection = db.connect(DB_PATH)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def test_row_counts(self):
        """SEED verisinin sayilari.

        Panelden olusturulan gosterim profili (M-TEST) seed'in parcasi degildir;
        sayimdan haric tutulur — bkz. core/demo_profile.py.
        """
        from core.demo_profile import TEST_MERCHANT_ID

        expected = {
            "commission_plans": 4, "merchants": 18, "pos_devices": 18,
            "settlements": 82, "transactions": 332, "kb_articles": 8,
            "merchant_monthly_volume": 108, "merchant_contacts": 54,
        }
        scoped = {"merchants": "merchant_id", "pos_devices": "merchant_id",
                  "settlements": "merchant_id", "transactions": "merchant_id",
                  "merchant_monthly_volume": "merchant_id",
                  "merchant_contacts": "merchant_id"}

        for table, count in expected.items():
            column = scoped.get(table)
            if table == "merchant_contacts":
                # Canli konusma kayitlari (source='ai'/'insight') seed sayimina
                # girmez; yalnizca seed kayitlari dogrulanir.
                actual = self.connection.execute(
                    "SELECT COUNT(*) FROM merchant_contacts "
                    "WHERE merchant_id != ? AND source = 'seed'",
                    (TEST_MERCHANT_ID,)).fetchone()[0]
            elif column:
                actual = self.connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE {column} != ?",
                    (TEST_MERCHANT_ID,)).fetchone()[0]
            else:
                actual = self.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            self.assertEqual(actual, count, f"{table} kayit sayisi (seed)")

    def test_no_foreign_key_violations(self):
        self.assertEqual(self.connection.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_stable_ids_preserved(self):
        """Testler ve demo senaryolari bu ID'lere bagli."""
        for table, column, value in (
            ("merchants", "merchant_id", "M-1001"),
            ("transactions", "txn_id", "TXN-88213"),
            ("settlements", "batch_id", "SET-8990"),
            ("pos_devices", "terminal_id", "TRM-4451"),
            ("commission_plans", "plan_id", "PLAN-RET"),
            ("kb_articles", "issue_id", "KB-POS-01"),
        ):
            row = self.connection.execute(
                f"SELECT 1 FROM {table} WHERE {column} = ?", (value,)).fetchone()
            self.assertIsNotNone(row, f"{table}.{column} = {value} kayip")

    def test_phone_numbers_are_normalized_and_unique(self):
        rows = self.connection.execute(
            "SELECT phone_e164 FROM merchants WHERE phone_e164 != ''").fetchall()
        values = [row["phone_e164"] for row in rows]
        self.assertEqual(len(values), len(set(values)), "tekrar eden telefon")
        for value in values:
            self.assertTrue(value.startswith("+"), f"normalize edilmemis: {value}")

    def test_kb_symptoms_have_normalized_column(self):
        """SQLite LOWER() Turkce 'I' cozemez; normalize kopya seed'de uretilir."""
        rows = self.connection.execute(
            "SELECT symptom, symptom_normalized FROM kb_symptoms").fetchall()
        self.assertGreater(len(rows), 0)
        for row in rows:
            self.assertEqual(row["symptom_normalized"], tr_lower(row["symptom"]))


class TestQueryPlans(unittest.TestCase):
    """Sicak sorgular index kullaniyor mu (tam tarama yok)."""

    @classmethod
    def setUpClass(cls):
        if not DB_PATH.exists():
            raise unittest.SkipTest("moka.sqlite3 yok")
        cls.connection = db.connect(DB_PATH)

    @classmethod
    def tearDownClass(cls):
        cls.connection.close()

    def _plan(self, sql, params=()):
        return " ".join(row["detail"] for row in
                        self.connection.execute(f"EXPLAIN QUERY PLAN {sql}", params))

    def test_transaction_day_lookup_uses_index(self):
        plan = self._plan(
            "SELECT * FROM transactions WHERE merchant_id = ? AND ts_day = ? "
            "ORDER BY ts DESC LIMIT 5", ("M-1001", "2026-07-19"))
        self.assertIn("idx_txn", plan)
        self.assertNotIn("SCAN transactions", plan)

    def test_card_last4_lookup_uses_index(self):
        plan = self._plan(
            "SELECT * FROM transactions WHERE merchant_id = ? AND card_last4 = ?",
            ("M-1001", "4832"))
        self.assertIn("idx_txn", plan)

    def test_settlement_lookup_uses_index(self):
        plan = self._plan(
            "SELECT * FROM settlements WHERE merchant_id = ? ORDER BY batch_date DESC LIMIT 5",
            ("M-1001",))
        self.assertIn("idx_settle", plan)
        self.assertNotIn("SCAN settlements", plan)

    def test_phone_lookup_uses_index(self):
        plan = self._plan("SELECT * FROM merchants WHERE phone_e164 = ?", ("+905321112233",))
        self.assertIn("idx_merchants_phone", plan)


class TestRepositoryQueries(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DB_PATH.exists():
            raise unittest.SkipTest("moka.sqlite3 yok")

    def setUp(self):
        self.repo = MerchantRepository(DB_PATH)

    def test_turkish_lowercase_kb_match(self):
        """'İşlem' -> 'işlem': SQLite LOWER() bunu yapamaz, normalize sutun yapar."""
        self.assertIsNotNone(self.repo.match_kb("İşlem geçmiyor, cihaz hata veriyor"))

    def test_pending_period_not_truncated_by_limit(self):
        """Eski surumde once limit uygulanip SONRA filtreleniyordu.

        SQL'de WHERE once kosar; bekleyen kayit limit yuzunden gozden kacmaz.
        """
        for row in self.repo._query(
                "SELECT DISTINCT merchant_id FROM settlements "
                "WHERE status IN ('planlandı','beklemede')"):
            pending = self.repo.get_settlements_for_period(row["merchant_id"], "pending")
            self.assertTrue(pending)
            for settlement in pending:
                self.assertIn(settlement["status"], ("planlandı", "beklemede"))

    def test_transaction_shape_matches_legacy_json(self):
        """Donen sozluk eski JSON alan adlarini tasimali (panel/testler buna bagli)."""
        txn = self.repo.find_transactions("M-1001", limit=1)[0]
        for field in ("txn_id", "merchant_id", "amount_try", "card_last4",
                      "timestamp", "status", "settlement_batch_id"):
            self.assertIn(field, txn)
        self.assertNotIn("ts", txn)        # ic sutun disari sizmamali
        self.assertNotIn("ts_token", txn)

    def test_fuzzy_amount_matching(self):
        txn = self.repo.find_transactions("M-1001", limit=1)[0]
        amount = txn["amount_try"]
        # ±1 TL tolerans: STT kurusu yanlis duyabilir
        self.assertTrue(self.repo.find_transactions("M-1001", amount_try=amount + 0.5))
        self.assertFalse(self.repo.find_transactions("M-1001", amount_try=amount + 500))

    def test_payment_link_is_persisted(self):
        """Eskiden uretilen link hicbir yere yazilmiyordu."""
        link = self.repo.create_payment_link("M-1001", amount_try=1250, description="test")
        rows = self.repo._query(
            "SELECT * FROM payment_links WHERE url = ?", (link["url"],))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["amount_try"], 1250)

    def test_identity_link_and_resolve(self):
        self.repo.link_identity("call-testxyz", "M-1007", kind="call")
        self.assertEqual(self.repo.resolve_identity("call-testxyz"), "M-1007")

    def test_find_merchant_by_phone(self):
        row = self.repo._query_one(
            "SELECT phone, merchant_id FROM merchants WHERE phone_e164 != '' LIMIT 1")
        merchant = self.repo.find_merchant_by_phone(row["phone"])
        self.assertIsNotNone(merchant)
        self.assertEqual(merchant["merchant_id"], row["merchant_id"])

    def test_missing_database_gives_actionable_error(self):
        with self.assertRaises(FileNotFoundError) as caught:
            MerchantRepository(BASE_DIR / "data" / "does-not-exist.sqlite3")
        self.assertIn("seed_demo_data.py", str(caught.exception))


class TestDateFreshness(unittest.TestCase):
    """Goreli tarihler gun degisince tazelenmeli (demo verisi bayatlamasin)."""

    @classmethod
    def setUpClass(cls):
        if not DB_PATH.exists():
            raise unittest.SkipTest("moka.sqlite3 yok")

    def test_ensure_fresh_recomputes_dates_after_day_change(self):
        repo = MerchantRepository(DB_PATH)
        with db.session(DB_PATH) as connection:
            connection.execute(
                "INSERT INTO app_config (key, value_json) VALUES ('resolved_on', ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (json.dumps("2020-01-01"),))
            connection.execute(
                "UPDATE transactions SET ts = '2020-01-01T00:00:00', ts_day = '2020-01-01' "
                "WHERE txn_id = 'TXN-88213'")

        self.assertTrue(repo.ensure_fresh())

        txn = repo._query_one("SELECT ts, ts_day FROM transactions WHERE txn_id = 'TXN-88213'")
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        self.assertTrue(txn["ts"].startswith(yesterday))
        self.assertEqual(txn["ts_day"], yesterday)

    def test_ensure_fresh_is_noop_when_already_current(self):
        repo = MerchantRepository(DB_PATH)
        repo.ensure_fresh()
        self.assertFalse(repo.ensure_fresh())

    def test_monthly_volume_anchored_to_current_month(self):
        repo = MerchantRepository(DB_PATH)
        repo.ensure_fresh()
        series = repo._volume_rows("M-1001")
        self.assertEqual(series[-1]["month"], date.today().strftime("%Y-%m"))


class TestCustomerCRM(unittest.TestCase):
    """CRM / musteri-360 katmani: risk skoru, portfoy listesi, 360, temas gecmisi."""

    @classmethod
    def setUpClass(cls):
        if not DB_PATH.exists():
            raise unittest.SkipTest("moka.sqlite3 yok")

    def setUp(self):
        self.repo = MerchantRepository(DB_PATH)
        # Yazma testleri paylasilan seed DB'ye dokunur; kendi eklediklerini
        # temizle ki merchant_contacts sayimi (test_row_counts) bozulmasin.
        self.addCleanup(self._cleanup_test_contacts)

    def _cleanup_test_contacts(self):
        with db.session(DB_PATH) as connection:
            connection.execute(
                "DELETE FROM merchant_contacts "
                "WHERE note LIKE 'crm-test-%' OR note = 'tazeleme testi' "
                "OR session_id LIKE 'crm-test-%'")

    @staticmethod
    def _merchant(volumes, plan=None, status="active"):
        return {
            "monthly_volume_try": [
                {"month": f"m{i}", "volume": v} for i, v in enumerate(volumes)],
            "plan": plan or {},
            "status": status,
        }

    def test_risk_zero_last_month_is_critical(self):
        risk = self.repo.risk_profile(self._merchant([100, 100, 100, 100, 100, 0]))
        self.assertEqual(risk["risk_score"], 95)
        self.assertEqual(risk["risk_tier"], "kritik")
        self.assertEqual(risk["segment"], "uyuyan")

    def test_risk_dormant_drop(self):
        risk = self.repo.risk_profile(self._merchant([100, 100, 100, 100, 100, 10]))
        self.assertGreaterEqual(risk["risk_score"], 80)
        self.assertEqual(risk["segment"], "uyuyan")

    def test_risk_healthy_growth_is_low(self):
        risk = self.repo.risk_profile(self._merchant([100, 110, 120, 130, 140, 170]))
        self.assertEqual(risk["risk_tier"], "düşük")
        self.assertEqual(risk["segment"], "büyüyor")

    def test_risk_retention_plan_adds_offset(self):
        base = self.repo.risk_profile(self._merchant([100, 110, 120, 130, 140, 145]))
        ret = self.repo.risk_profile(self._merchant(
            [100, 110, 120, 130, 140, 145], plan={"retention_only": True}))
        self.assertEqual(ret["risk_score"], base["risk_score"] + 10)

    def test_risk_suspended_status_floor(self):
        risk = self.repo.risk_profile(self._merchant(
            [100, 110, 120, 130, 140, 150], status="askıda"))
        self.assertGreaterEqual(risk["risk_score"], 80)

    def test_list_customers_excludes_test_profile(self):
        from core.demo_profile import TEST_MERCHANT_ID

        customers = self.repo.list_customers()
        ids = {c["merchant_id"] for c in customers}
        self.assertEqual(len(customers), 18)
        self.assertNotIn(TEST_MERCHANT_ID, ids)
        for field in ("business_name", "risk_score", "risk_tier", "segment",
                      "last_month_try", "change_pct", "plan_name", "last_contact_at"):
            self.assertIn(field, customers[0])

    def test_portfolio_summary_shape(self):
        summary = self.repo.portfolio_summary()
        self.assertEqual(summary["merchant_count"], 18)
        self.assertEqual(len(summary["monthly_totals"]), 6)
        for field in ("count_by_tier", "count_by_risk_tier", "count_by_segment",
                      "plan_distribution", "top_growing", "top_dormant",
                      "txn_volume_by_month", "commission_by_month"):
            self.assertIn(field, summary)
        # Portfoy son-ay toplami, musteri listesindeki son-ay toplamiyla tutarli
        expected_last = sum(c["last_month_try"] for c in self.repo.list_customers())
        self.assertEqual(summary["total_last_month_try"], expected_last)

    def test_customer_360_assembles_all_sections(self):
        data = self.repo.get_customer_360("M-1001")
        self.assertIsNotNone(data)
        for section in ("merchant", "plan", "devices", "volume_trend", "risk",
                        "settlements", "transactions", "monthly", "contacts"):
            self.assertIn(section, data)
        self.assertEqual(data["merchant"]["merchant_id"], "M-1001")

    def test_customer_360_unknown_is_none(self):
        self.assertIsNone(self.repo.get_customer_360("M-DOES-NOT-EXIST"))

    def test_contact_round_trip(self):
        marker = f"crm-test-{date.today().isoformat()}"
        self.repo.add_contact("M-1007", channel="telefon", note=marker,
                              rep="Test Temsilci", subject="birim testi")
        contacts = self.repo.list_contacts("M-1007")
        self.assertTrue(any(c["note"] == marker for c in contacts))
        top = contacts[0]
        self.assertNotIn("contacted_token", top)   # ic sutun disari sizmamali
        self.assertIn("contacted_at", top)

    def test_ensure_fresh_refreshes_contact_dates(self):
        self.repo.add_contact("M-1011", channel="whatsapp", note="tazeleme testi")
        with db.session(DB_PATH) as connection:
            connection.execute(
                "INSERT INTO app_config (key, value_json) VALUES ('resolved_on', ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
                (json.dumps("2020-01-01"),))
            connection.execute(
                "UPDATE merchant_contacts SET contacted_at = '2020-01-01T00:00:00' "
                "WHERE merchant_id = 'M-1011'")
        self.assertTrue(self.repo.ensure_fresh())
        today = date.today().isoformat()
        # Ekledigim D0-token temas bugune cozulmeli
        mine = self.repo._query(
            "SELECT contacted_at FROM merchant_contacts "
            "WHERE merchant_id = 'M-1011' AND note = 'tazeleme testi'")
        self.assertTrue(mine)
        self.assertTrue(all(r["contacted_at"].startswith(today) for r in mine))
        # Bayat 2020 tarihleri token'lardan yeniden cozulmus olmali (hicbiri kalmamali)
        stale = self.repo._query(
            "SELECT 1 FROM merchant_contacts "
            "WHERE merchant_id = 'M-1011' AND contacted_at LIKE '2020%'")
        self.assertFalse(stale)

    def test_identities_for_merchant(self):
        self.repo.link_identity("crm-bridge-test", "M-1012", kind="manual")
        self.assertIn("crm-bridge-test", self.repo.list_identities_for_merchant("M-1012"))

    def test_session_contact_upserts_not_duplicates(self):
        """Ayni oturum tekrar yazilinca YENI satir acmaz, gunceller."""
        sid = "crm-test-sess-1"
        self.repo.upsert_session_contact("M-1007", sid, channel="whatsapp",
                                         subject="Cihaz arızası", note="ilk özet")
        self.repo.upsert_session_contact("M-1007", sid, channel="whatsapp",
                                         subject="Cihaz arızası", note="güncel özet")
        mine = [c for c in self.repo.list_contacts("M-1007", limit=99)
                if c.get("session_id") == sid]
        self.assertEqual(len(mine), 1)              # tek kayit
        self.assertEqual(mine[0]["note"], "güncel özet")
        self.assertEqual(mine[0]["source"], "ai")

    def test_insight_recorded_and_listed(self):
        self.repo.add_insight("M-1012", "taşınma", "crm-test-dükkanı taşımış",
                              session_id="crm-test-sess-2")
        insights = self.repo.list_insights("M-1012")
        self.assertTrue(any(i["note"] == "crm-test-dükkanı taşımış" for i in insights))
        self.assertEqual(insights[0]["source"], "insight")

    def test_session_insight_upserts_by_category(self):
        """Firsat notu oturum+kategori bazli tek kayit; her turda cogalmaz."""
        sid = "crm-test-sess-opp"
        self.repo.upsert_session_insight("M-1007", sid, "fırsat", "crm-test-POS Pro ilgisi v1")
        self.repo.upsert_session_insight("M-1007", sid, "fırsat", "crm-test-POS Pro ilgisi v2")
        opps = [i for i in self.repo.list_insights("M-1007")
                if i.get("session_id") == sid and i["subject"] == "fırsat"]
        self.assertEqual(len(opps), 1)                 # tek firsat kaydi
        self.assertEqual(opps[0]["note"], "crm-test-POS Pro ilgisi v2")

    def test_session_contact_outcome_and_sentiment(self):
        """Cozum durumu + ruh hali temas kaydina yansir."""
        sid = "crm-test-outcome"
        self.repo.upsert_session_contact("M-1007", sid, subject="Cihaz arızası",
            note="crm-test", outcome="çözüldü", sentiment="sakin")
        row = [c for c in self.repo.list_contacts("M-1007", limit=99)
               if c.get("session_id") == sid][0]
        self.assertEqual(row["outcome"], "çözüldü")
        self.assertEqual(row["sentiment"], "sakin")

    def test_update_preferred_channel(self):
        before = self.repo.get_merchant("M-1013")["preferred_channel"]
        self.addCleanup(lambda: self.repo.update_preferred_channel("M-1013", before)
                        if before in ("telefon", "whatsapp", "email", "sms") else None)
        self.repo.update_preferred_channel("M-1013", "sms")
        self.assertEqual(self.repo.get_merchant("M-1013")["preferred_channel"], "sms")
        self.repo.update_preferred_channel("M-1013", "gecersiz")   # yok sayilir
        self.assertEqual(self.repo.get_merchant("M-1013")["preferred_channel"], "sms")

    def test_insights_excluded_from_seed_count(self):
        """Canli kayitlar source='seed' degil; seed sayimina karismaz."""
        before = self.repo._query(
            "SELECT COUNT(*) c FROM merchant_contacts WHERE source='seed'")[0]["c"]
        self.repo.add_insight("M-1011", "rakip", "crm-test-rakip teklifi",
                              session_id="crm-test-sess-3")
        after = self.repo._query(
            "SELECT COUNT(*) c FROM merchant_contacts WHERE source='seed'")[0]["c"]
        self.assertEqual(before, after)             # seed sayisi degismedi


class TestNormalizeDay(unittest.TestCase):
    def test_relative_turkish_days(self):
        today = date(2026, 7, 15)   # Carsamba
        self.assertEqual(normalize_day("bugün", today), "2026-07-15")
        self.assertEqual(normalize_day("dün", today), "2026-07-14")
        self.assertEqual(normalize_day("D-2", today), "2026-07-13")
        self.assertEqual(normalize_day("salı günü", today), "2026-07-14")
        self.assertIsNone(normalize_day("bilinmeyen", today))


class TestSeedScript(unittest.TestCase):
    def test_seed_is_idempotent_without_force(self):
        result = subprocess.run(
            [sys.executable, "scripts/seed_demo_data.py"],
            cwd=BASE_DIR, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("zaten dolu", result.stdout)


if __name__ == "__main__":
    unittest.main()
