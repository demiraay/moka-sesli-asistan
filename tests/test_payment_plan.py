import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.payment_plan import build_payment_plan


class TestPaymentPlan(unittest.TestCase):
    def test_basic_plan_without_extras(self):
        plan = build_payment_plan(
            price=10_000_000,
            down_payment_pct=25,
            months=24,
            start=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )

        self.assertEqual(plan["down_payment"], 2_500_000)
        self.assertEqual(plan["financed"], 7_500_000)
        self.assertEqual(plan["monthly"], 312_500)
        self.assertEqual(plan["total"], 10_000_000)
        # Pesinat + 24 taksit
        self.assertEqual(len(plan["schedule"]), 25)
        self.assertEqual(plan["schedule"][0]["label"], "Peşinat")
        self.assertEqual(plan["schedule"][1]["month_label"], "Ağustos 2026")
        # Takvim toplami genel toplama esit olmali
        self.assertEqual(sum(row["amount"] for row in plan["schedule"]), plan["total"])

    def test_balloons_are_evenly_spaced_and_totals_match(self):
        plan = build_payment_plan(
            price=12_000_000,
            down_payment_pct=20,
            months=24,
            balloon_count=3,
            balloon_amount=1_000_000,
            start=datetime(2026, 7, 10, tzinfo=timezone.utc),
        )

        self.assertEqual(plan["balloon_total"], 3_000_000)
        self.assertEqual(plan["financed"], 12_000_000 - 2_400_000 - 3_000_000)
        balloon_rows = [row for row in plan["schedule"] if row["label"] == "Ara ödeme"]
        self.assertEqual(len(balloon_rows), 3)
        self.assertEqual(sum(row["amount"] for row in plan["schedule"]), plan["total"])

    def test_interest_adds_vade_farki(self):
        plan = build_payment_plan(
            price=10_000_000,
            down_payment_pct=50,
            months=12,
            annual_rate_pct=24,
        )

        # 5M kalan, yillik %24, 12 ay -> 1.2M vade farki
        self.assertEqual(plan["interest"], 1_200_000)
        self.assertEqual(plan["financed"], 6_200_000)
        self.assertEqual(plan["total"], 11_200_000)

    def test_rounding_is_absorbed_by_last_installment(self):
        plan = build_payment_plan(price=10_000_000, down_payment_pct=0, months=7)

        amounts = [row["amount"] for row in plan["schedule"] if "taksit" in row["label"]]
        self.assertEqual(sum(amounts), 10_000_000)
        self.assertEqual(amounts[-1], plan["last_monthly"])

    def test_validation_errors(self):
        with self.assertRaises(ValueError):
            build_payment_plan(price=0, down_payment_pct=10, months=12)
        with self.assertRaises(ValueError):
            build_payment_plan(price=1_000_000, down_payment_pct=120, months=12)
        with self.assertRaises(ValueError):
            build_payment_plan(price=1_000_000, down_payment_pct=10, months=0)
        with self.assertRaises(ValueError):
            # Pesinat + ara odemeler fiyati asiyor
            build_payment_plan(price=1_000_000, down_payment_pct=50, months=12, balloon_count=2, balloon_amount=400_000)


if __name__ == "__main__":
    unittest.main()
