"""Optional regression checks for the supplied train.csv; CI skips absent source data."""
import csv
from decimal import Decimal
from pathlib import Path
import unittest

import insight_generator as app

DATA = Path(__file__).resolve().parents[1] / "data" / "train.csv"


@unittest.skipUnless(DATA.exists(), "User-supplied CSV is not redistributed")
class SuppliedDatasetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame, cls.audit = app.clean_data(DATA)
        cls.metrics, cls.tables = app.calculate_metrics(cls.frame)

    def test_source_profile(self):
        self.assertEqual(self.audit["rows"], 9800)
        self.assertEqual(self.audit["missing_postal_codes"], 11)
        self.assertEqual(self.audit["exact_duplicates"], 0)
        self.assertEqual(self.audit["orders"], 4922)
        self.assertEqual(self.audit["customers"], 793)

    def test_dates(self):
        self.assertEqual(self.audit["date_min"], "2015-01-03")
        self.assertEqual(self.audit["date_max"], "2018-12-30")

    def test_total_independent_decimal_control(self):
        with DATA.open(encoding="utf-8", newline="") as source:
            total = sum((Decimal(row["Sales"]) for row in csv.DictReader(source)), Decimal(0))
        self.assertEqual(total, Decimal("2261536.7827"))
        self.assertLess(abs(Decimal(str(self.metrics["total_sales"])) - total), Decimal("0.000001"))

    def test_region_and_category(self):
        self.assertEqual(self.metrics["top_region"]["label"], "West")
        self.assertEqual(app.money(self.metrics["top_region"]["sales"]), "$710,219.68")
        self.assertEqual(self.metrics["top_category"]["label"], "Technology")
        self.assertEqual(app.money(self.metrics["top_category"]["sales"]), "$827,455.87")

    def test_month_and_customer(self):
        self.assertEqual(self.metrics["top_month"]["label"], "2018-11")
        self.assertEqual(app.money(self.metrics["top_month"]["sales"]), "$117,938.16")
        self.assertEqual(self.metrics["top_customer"]["label"], "Sean Miller")
        self.assertEqual(app.money(self.metrics["top_customer"]["sales"]), "$25,043.05")

    def test_customer_share(self):
        self.assertAlmostEqual(self.metrics["top_ten_customer_share_percent"], 6.8011791882671995)
        cohort = self.metrics["top_ten_percent_customers"]
        self.assertEqual(cohort["customer_count"], 80)
        self.assertEqual(cohort["total_customers"], 793)
        self.assertAlmostEqual(cohort["sales_share_percent"], 31.160099910410356)
        self.assertEqual(int(self.tables["customer"]["Top ten percent cohort"].sum()), 80)
        # Independent raw CSV control verifies both resume findings.
        totals = {}
        selected_sales = Decimal(0)
        total = Decimal(0)
        with DATA.open(encoding="utf-8", newline="") as source:
            for row in csv.DictReader(source):
                sale = Decimal(row["Sales"])
                total += sale
                totals[row["Customer ID"]] = totals.get(row["Customer ID"], Decimal(0)) + sale
                if int(row["Order Date"].split("/")[1]) in [9, 11, 12]:
                    selected_sales += sale
        control_cohort = sum(sorted(totals.values(), reverse=True)[:80])
        self.assertAlmostEqual(cohort["sales_share_percent"], float(control_cohort / total * 100))
        season = self.metrics["seasonal_concentration"]
        self.assertAlmostEqual(season["sales_share_percent"], float(selected_sales / total * 100))
        self.assertAlmostEqual(season["sales_share_percent"], 42.96836114422398)
        yearly = self.tables["seasonality"]
        self.assertAlmostEqual(yearly["Selected-month sales"].sum(), float(selected_sales))
        self.assertAlmostEqual(yearly["Annual sales"].sum(), float(total))


if __name__ == "__main__":
    unittest.main()
