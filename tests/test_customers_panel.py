"""Musteriler (CRM 360) + Raporlar panel route'lari."""

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from admin_panel.app import create_app
from core.admin_store import AdminStore
from core.orchestrator import AgentOrchestrator
from core import pdf_report

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "moka.sqlite3"


class TestCustomersPanel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not DB_PATH.exists():
            raise unittest.SkipTest("moka.sqlite3 yok; once seed_demo_data.py calistirin")

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        base = Path(self.temp_dir.name)
        shutil.copytree(BASE_DIR / "data", base / "data",
                        ignore=shutil.ignore_patterns("admin.sqlite3", "uploads"))
        self.store = AdminStore(base_dir=str(base),
                                db_path=str(base / "admin_test.sqlite3"))
        orchestrator = AgentOrchestrator()
        orchestrator.admin_store = self.store
        self.app = create_app(store=self.store, orchestrator=orchestrator)
        self.client = self.app.test_client()

    def test_customers_list_renders(self):
        resp = self.client.get("/admin/customers")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"M-1001", resp.data)
        self.assertIn("Müşteriler".encode(), resp.data)

    def test_customer_detail_renders_all_sections(self):
        resp = self.client.get("/admin/customers/M-1001")
        self.assertEqual(resp.status_code, 200)
        body = resp.data
        for token in ("Aylık ciro trendi".encode(), "Son hakedişler".encode(),
                      "POS cihazları".encode(), "Temas geçmişi".encode(),
                      "Konuşmalar".encode()):
            self.assertIn(token, body)

    def test_customer_detail_unknown_is_404(self):
        self.assertEqual(self.client.get("/admin/customers/M-NOPE").status_code, 404)

    def test_reports_renders(self):
        resp = self.client.get("/admin/reports")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Portföy".encode(), resp.data)

    def test_customers_csv_export(self):
        resp = self.client.get("/admin/customers/export.csv")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.mimetype.startswith("text/csv"))
        self.assertIn("attachment", resp.headers.get("Content-Disposition", ""))
        self.assertIn(b"M-1001", resp.data)

    def test_single_customer_csv_export(self):
        resp = self.client.get("/admin/customers/M-1001/export.csv")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.mimetype.startswith("text/csv"))

    def test_report_csv_export(self):
        resp = self.client.get("/admin/reports/export.csv")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.mimetype.startswith("text/csv"))

    def test_customers_tier_filter(self):
        """kurumsal filtresi buyuk hesaplari birakir, mikro'yu eler."""
        resp = self.client.get("/admin/customers?tier=kurumsal")
        self.assertEqual(resp.status_code, 200)
        self.assertIn(b"M-1017", resp.data)     # kurumsal
        self.assertNotIn(b"M-1007", resp.data)  # mikro

    @unittest.skipUnless(pdf_report.is_available(), "pdflatex yok")
    def test_customer_pdf_report(self):
        resp = self.client.get("/admin/customers/M-1007/report.pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "application/pdf")
        self.assertTrue(resp.data.startswith(b"%PDF"))

    @unittest.skipUnless(pdf_report.is_available(), "pdflatex yok")
    def test_portfolio_pdf_report(self):
        resp = self.client.get("/admin/reports/report.pdf")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, "application/pdf")
        self.assertTrue(resp.data.startswith(b"%PDF"))

    def test_customer_pdf_unknown_is_404(self):
        self.assertEqual(
            self.client.get("/admin/customers/M-NOPE/report.pdf").status_code, 404)

    def test_crm_insight_shows_in_360(self):
        """Kapali dongu paneli: agent'in kaydettigi icgoru 360'ta gorunur."""
        from core import db
        from core.repository import MerchantRepository

        repo = MerchantRepository(str(DB_PATH))
        repo.add_insight("M-1013", "büyüme", "crm-test-yeni şube açtı",
                         session_id="crm-test-panel-1")

        def _cleanup():
            with db.session(str(DB_PATH)) as connection:
                connection.execute(
                    "DELETE FROM merchant_contacts WHERE session_id LIKE 'crm-test-%'")
        self.addCleanup(_cleanup)

        html = self.client.get("/admin/customers/M-1013").data.decode()
        self.assertIn("CRM İçgörüleri", html)
        self.assertIn("crm-test-yeni şube açtı", html)


if __name__ == "__main__":
    unittest.main()
