"""core/pdf_report: LaTeX kacisi, rapor uretimi ve (varsa) pdflatex derlemesi."""

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import pdf_report

_SAMPLE_CUSTOMER = {
    "merchant": {
        "merchant_id": "M-1007", "business_name": "Yıldız Cafe & Bistro",
        "owner_name": "Ayşe Yıldız", "sector": "Kafe", "city": "İstanbul",
        "district": "Moda", "phone": "+905387772233", "email": "ayse@x.com",
        "iban_masked": "TR** **73 91", "joined": "2023-06-15", "tier": "mikro",
        "preferred_channel": "telefon", "account_manager": "Deniz Yılmaz",
        "status": "active", "notes": "Churn riski var.",
        "products": ["fiziksel_pos"],
        "monthly_volume_try": [{"month": "2026-02", "volume": 140000},
                               {"month": "2026-07", "volume": 8000}],
    },
    "plan": {"name": "Standart", "rate_pct": 2.49},
    "risk": {"risk_score": 85, "risk_tier": "kritik", "segment": "uyuyan",
             "reasons": ["Ciro %89 dustu"]},
    "volume_trend": {"last_month": 8000, "prev_3m_avg": 90000, "change_pct": -88.6},
    "monthly": {"commission_try": 199, "rate_pct": 2.49},
    "upgrade": None,
    "settlements": [{"batch_id": "SET-8801", "batch_date": "2026-07-12",
                     "gross_try": 850, "status": "ödendi"}],
    "transactions": [{"timestamp": "2026-07-12T13:44:00", "amount_try": 850,
                      "channel": "pos", "status": "onaylandı"}],
    "contacts": [{"contacted_at": "2026-07-18", "channel": "ziyaret",
                  "subject": "Cihaz arıza takibi", "rep": "Deniz Yılmaz",
                  "note": "Teknik ekip yönlendirildi.", "source": "seed"}],
    "insights": [{"contacted_at": "2026-07-19", "subject": "taşınma", "rep": "AI",
                  "source": "insight",
                  "note": "Dükkanı yan sokağa taşıdı, yeni yer daha sakin."}],
}

_SAMPLE_SUMMARY = {
    "merchant_count": 18, "total_last_month_try": 3110500, "total_commission_try": 62856,
    "monthly_totals": [{"month": "2026-02", "volume_try": 3050000, "commission_try": 60000},
                       {"month": "2026-07", "volume_try": 3110500, "commission_try": 62856}],
    "commission_by_month": [{"month": "2026-07", "commission_try": 62856}],
    "txn_volume_by_month": [{"month": "2026-06", "count": 15}, {"month": "2026-07", "count": 317}],
    "count_by_tier": {"kurumsal": 2, "orta": 5, "mikro": 11},
    "count_by_risk_tier": {"düşük": 14, "kritik": 4},
    "count_by_segment": {"büyüyor": 2, "stabil": 12, "uyuyan": 4},
    "plan_distribution": {"Standart": 11, "Sadakat (3 ay)": 1},
    "top_growing": [{"merchant_id": "M-1001", "business_name": "Demiray Kuruyemiş",
                     "last_month_try": 182000, "change_pct": 26.0}],
    "top_dormant": [{"merchant_id": "M-1015", "business_name": "Taze Manav",
                     "last_month_try": 4000, "risk_tier": "kritik", "risk_score": 95,
                     "segment": "uyuyan"}],
}


class TestLatexEscape(unittest.TestCase):
    def test_special_chars_escaped(self):
        self.assertEqual(pdf_report.esc("a & b_c %50 #1 $x"),
                         r"a \& b\_c \%50 \#1 \$x")

    def test_none_is_empty(self):
        self.assertEqual(pdf_report.esc(None), "")

    def test_turkish_passthrough(self):
        # Turkce karakterler T1 fontenc ile dogrudan gecer (escape edilmez)
        self.assertEqual(pdf_report.esc("İşğ"), "İşğ")


class TestReportRendering(unittest.TestCase):
    """Derleme gerektirmez: LaTeX kaynagi dogru sekilde uretiliyor mu."""

    def test_customer_report_is_valid_latex_source(self):
        tex = pdf_report.render_customer_report(
            _SAMPLE_CUSTOMER, project_name="Moka", generated_on="21.07.2026")
        self.assertIn(r"\documentclass", tex)
        self.assertIn(r"\end{document}", tex)
        self.assertIn("Yıldız Cafe", tex)
        self.assertIn("Müşteri Raporu", tex)      # footer
        self.assertIn("AI İçgörüleri", tex)       # cikarilan notlar raporda
        self.assertIn("taşınma", tex)

    def test_portfolio_report_is_valid_latex_source(self):
        tex = pdf_report.render_portfolio_report(
            _SAMPLE_SUMMARY, project_name="Moka", generated_on="21.07.2026")
        self.assertIn(r"\documentclass", tex)
        self.assertIn(r"\end{document}", tex)
        self.assertIn("Portföy Raporu", tex)

    def test_missing_sections_do_not_crash(self):
        sparse = {"merchant": {"merchant_id": "M-X", "business_name": "Boş"},
                  "risk": {}, "volume_trend": {}, "monthly": {}, "plan": {}}
        tex = pdf_report.render_customer_report(
            sparse, project_name="Moka", generated_on="01.01.2026")
        self.assertIn(r"\end{document}", tex)


@unittest.skipUnless(pdf_report.is_available(), "pdflatex yok — derleme testi atlanir")
class TestPdfCompilation(unittest.TestCase):
    """Gercek pdflatex derlemesi (TeX Live kuruluysa)."""

    def test_customer_pdf_compiles(self):
        pdf = pdf_report.customer_pdf(_SAMPLE_CUSTOMER, project_name="Moka")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 5000)

    def test_portfolio_pdf_compiles(self):
        pdf = pdf_report.portfolio_pdf(_SAMPLE_SUMMARY, project_name="Moka")
        self.assertTrue(pdf.startswith(b"%PDF"))
        self.assertGreater(len(pdf), 5000)


if __name__ == "__main__":
    unittest.main()
