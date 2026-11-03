"""Offline regression tests use synthetic data; no model or private dataset required."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import URLError

import pandas as pd

import insight_generator as app


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "input.csv"
        self.raw = pd.DataFrame({
            "Row ID": [1, 2, 3], "Order ID": ["A", "A", "B"],
            "Order Date": ["08/11/2017", "08/11/2017", "02/01/2018"],
            "Ship Date": ["11/11/2017", "11/11/2017", "05/01/2018"],
            "Customer ID": ["AA", "AA", "BB"], "Customer Name": ["Alice", "Alice", "Bob"],
            "Postal Code": ["01234", None, "90210"], "City": ["A", "A", "B"],
            "State": ["MA", "MA", "CA"], "Region": ["East", "East", "West"],
            "Category": ["Technology", "Technology", "Furniture"], "Sales": [100.25, 200.50, 50.00]})
        self.save()

    def save(self):
        self.raw.to_csv(self.path, index=False)

    def prepared(self):
        frame, audit = app.clean_data(self.path)
        metrics, tables = app.calculate_metrics(frame)
        facts = app.facts_for_narrative(metrics)
        return audit, metrics, tables, facts

    def test_day_first_dates(self):
        frame, _ = app.clean_data(self.path)
        self.assertEqual(frame.iloc[0]["Order Date"], pd.Timestamp("2017-11-08"))

    def test_currency_rounding_explicit(self):
        self.assertEqual(app.money(117938.155), "$117,938.16")

    def test_postal_missing_retained_and_flagged(self):
        frame, audit = app.clean_data(self.path)
        self.assertEqual(audit["missing_postal_codes"], 1)
        self.assertEqual(len(frame), 3)
        self.assertTrue(frame.iloc[1]["Postal Code Missing"])
        self.assertTrue(pd.isna(frame.iloc[1]["Postal Code"]))
        self.assertEqual(frame.iloc[0]["Postal Code"], "01234")

    def test_duplicate_rejected(self):
        self.raw = pd.concat([self.raw, self.raw.iloc[[0]]])
        self.save()
        with self.assertRaises(app.ValidationError):
            app.clean_data(self.path)

    def test_duplicate_row_id_rejected(self):
        self.raw.loc[1, "Row ID"] = 1
        self.save()
        with self.assertRaises(app.ValidationError):
            app.clean_data(self.path)

    def test_repeat_orders_are_not_duplicates(self):
        _, audit = app.clean_data(self.path)
        self.assertEqual(audit["orders"], 2)

    def test_missing_column_rejected(self):
        self.raw = self.raw.drop(columns="Sales")
        self.save()
        with self.assertRaises(app.ValidationError):
            app.clean_data(self.path)

    def test_empty_rejected(self):
        self.raw = self.raw.iloc[:0]
        self.save()
        with self.assertRaises(app.ValidationError):
            app.clean_data(self.path)

    def test_missing_required_value_rejected(self):
        self.raw.loc[0, "Region"] = None
        self.save()
        with self.assertRaises(app.ValidationError):
            app.clean_data(self.path)

    def test_bad_date_rejected(self):
        self.raw.loc[0, "Order Date"] = "31/02/2017"
        self.save()
        with self.assertRaises(app.ValidationError):
            app.clean_data(self.path)

    def test_shipping_before_order_rejected(self):
        self.raw.loc[0, "Ship Date"] = "01/01/2017"
        self.save()
        with self.assertRaises(app.ValidationError):
            app.clean_data(self.path)

    def test_invalid_sales_rejected(self):
        for value in ["oops", "inf", "-1", "NaN"]:
            with self.subTest(value=value):
                self.raw["Sales"] = self.raw["Sales"].astype(str)
                self.raw.loc[0, "Sales"] = value
                self.save()
                with self.assertRaises(app.ValidationError):
                    app.clean_data(self.path)

    def test_zero_total_rejected(self):
        self.raw["Sales"] = 0
        self.save()
        with self.assertRaises(app.ValidationError):
            app.clean_data(self.path)

    def test_bad_postal_rejected(self):
        self.raw.loc[0, "Postal Code"] = "not a ZIP"
        self.save()
        with self.assertRaises(app.ValidationError):
            app.clean_data(self.path)

    def test_reconciliation_and_leaders(self):
        _, metrics, tables, _ = self.prepared()
        self.assertEqual(metrics["total_sales"], 350.75)
        self.assertEqual(metrics["top_region"]["label"], "East")
        self.assertEqual(metrics["top_category"]["sales"], 300.75)
        self.assertEqual(metrics["top_month"]["label"], "2017-11")
        self.assertEqual(metrics["top_customer"]["label"], "Alice")
        self.assertEqual(metrics["top_ten_customer_share_percent"], 100)
        for name, table in tables.items():
            if name == "seasonality":
                continue
            self.assertAlmostEqual(table["Sales"].sum(), 350.75)
        season = metrics["seasonal_concentration"]
        cohort = metrics["top_ten_percent_customers"]
        self.assertAlmostEqual(season["sales_share_percent"], 300.75 / 350.75 * 100)
        self.assertEqual(cohort["customer_count"], 1)
        self.assertAlmostEqual(cohort["sales_share_percent"], 300.75 / 350.75 * 100)
        self.assertEqual(int(tables["customer"]["Top ten percent cohort"].sum()), 1)
        self.assertEqual(len(tables["calendar_month"]), 12)
        by_year = tables["seasonality"].set_index("Year")
        self.assertEqual(by_year.loc[2017, "Selected-month share percent"], 100)
        self.assertEqual(by_year.loc[2018, "Selected-month share percent"], 0)
        self.assertAlmostEqual(by_year["Selected-month sales"].sum(), season["sales"])

    def test_customer_identity_conflict_rejected(self):
        self.raw.loc[1, "Customer Name"] = "Other"
        self.save()
        frame, _ = app.clean_data(self.path)
        with self.assertRaises(app.ValidationError):
            app.calculate_metrics(frame)

    def test_ties_disclosed(self):
        self.raw.loc[2, "Sales"] = 300.75
        self.save()
        _, metrics, _, facts = self.prepared()
        self.assertEqual(metrics["top_region"]["tied_leaders"], 2)
        self.assertIn("a joint leader", facts[0]["allowed_sentences"][0])
        frame, _ = app.clean_data(self.path)
        # Boundary sizes and equal sales: exact cohort size, deterministic ID ties.
        for size, expected in [(1, 1), (10, 1), (11, 2), (20, 2)]:
            with self.subTest(customer_count=size):
                sample = pd.concat([frame.iloc[[0]]] * size, ignore_index=True)
                sample["Customer ID"] = [f"ID-{i:03}" for i in reversed(range(size))]
                sample["Sales"] = 10.0
                result, tables = app.calculate_metrics(sample)
                cohort = result["top_ten_percent_customers"]
                self.assertEqual(cohort["customer_count"], expected)
                self.assertAlmostEqual(cohort["sales_share_percent"], expected / size * 100)
                flagged = tables["customer"].loc[tables["customer"]["Top ten percent cohort"], "Customer ID"].tolist()
                self.assertEqual(flagged, [f"ID-{i:03}" for i in range(expected)])

    def test_yoy_correct_and_first_unavailable(self):
        _, _, tables, _ = self.prepared()
        annual = tables["annual"]
        self.assertTrue(pd.isna(annual.iloc[0]["YoY percent"]))
        self.assertAlmostEqual(annual.iloc[1]["YoY percent"], (50 / 300.75 - 1) * 100)

    def test_missing_year_does_not_get_yoy(self):
        self.raw.loc[2, "Order Date"] = "02/01/2019"
        self.raw.loc[2, "Ship Date"] = "05/01/2019"
        self.save()
        _, _, tables, _ = self.prepared()
        self.assertTrue(tables["annual"]["YoY percent"].isna().all())

    def test_valid_narrative(self):
        *_, facts = self.prepared()
        for choice in [0, 1]:
            sentences = [f["allowed_sentences"][choice] for f in facts]
            self.assertTrue(app.validate_narrative(sentences, facts)["passed"])

    def test_wrong_number_rejected(self):
        *_, facts = self.prepared()
        sentences = [f["allowed_sentences"][0] for f in facts]
        sentences[0] = sentences[0].replace("300.75", "300.76")
        with self.assertRaises(app.ValidationError):
            app.validate_narrative(sentences, facts)

    def test_extra_number_or_claim_rejected(self):
        *_, facts = self.prepared()
        for suffix in [" Growth was 20%.", " Demand caused this result."]:
            sentences = [f["allowed_sentences"][0] for f in facts]
            sentences[0] += suffix
            with self.assertRaises(app.ValidationError):
                app.validate_narrative(sentences, facts)

    def test_entity_rank_currency_mutations_rejected(self):
        *_, facts = self.prepared()
        for old, new in [("East", "West"), ("leader", "laggard"), ("$", "€")]:
            sentences = [f["allowed_sentences"][0] for f in facts]
            sentences[0] = sentences[0].replace(old, new)
            with self.assertRaises(app.ValidationError):
                app.validate_narrative(sentences, facts)

    def test_wrong_sentence_count_rejected(self):
        *_, facts = self.prepared()
        with self.assertRaises(app.ValidationError):
            app.validate_narrative(["Sales were $300.75."], facts)

    def test_prompt_has_structured_evidence(self):
        *_, facts = self.prepared()
        prompt = app.build_prompt(facts)
        self.assertIn("Do not calculate", prompt)
        self.assertIn('"amount": "$300.75"', prompt)

    def test_template_end_to_end_audit(self):
        report = app.run_report(self.path, Path(self.temp.name) / "out", mode="template")
        self.assertIn("$350.75", report.read_text(encoding="utf-8"))
        run = json.loads((report.parent / "run.json").read_text())
        self.assertEqual(run["mode"], "template")
        self.assertTrue(run["generation"]["validation"]["passed"])
        self.assertEqual(len(run["input_sha256"]), 64)
        self.assertEqual(len(list(report.parent.glob("*_summary.csv"))), 7)
        metrics = json.loads((report.parent / "metrics.json").read_text())
        actions = json.loads((report.parent / "recommendations.json").read_text())
        self.assertEqual(actions, app.recommendations(metrics))
        self.assertIn("retention pilot", report.read_text(encoding="utf-8"))
        self.assertIn("inventory reviews", report.read_text(encoding="utf-8"))

    def test_html_escapes_entities(self):
        self.raw.loc[0:1, "Customer Name"] = "<script>alert</script>"
        self.save()
        report = app.run_report(self.path, Path(self.temp.name) / "out", mode="template")
        html = report.read_text(encoding="utf-8")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def response(self, payload):
        mock = unittest.mock.MagicMock()
        mock.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        return mock

    def test_ollama_retry_then_success(self):
        *_, facts = self.prepared()
        good = {"done": True, "response": json.dumps({"sentences": [f["allowed_sentences"][0] for f in facts]})}
        with patch.object(app, "urlopen", side_effect=[self.response({"done": True, "response": "bad json"}), self.response(good)]):
            _, meta = app.generate_narrative(facts)
        self.assertEqual(meta["attempts"], 2)
        self.assertEqual(len(meta["rejected_attempts"]), 1)

    def test_ollama_failure_publishes_nothing(self):
        output = Path(self.temp.name) / "failed"
        with patch.object(app, "urlopen", return_value=self.response({"done": True, "response": "{}"})):
            with self.assertRaises(app.ValidationError):
                app.run_report(self.path, output)
        self.assertFalse(output.exists())

    def test_ollama_unreachable_is_explicit(self):
        *_, facts = self.prepared()
        with patch.object(app, "urlopen", side_effect=URLError("offline")):
            with self.assertRaisesRegex(RuntimeError, "Cannot reach local Ollama"):
                app.generate_narrative(facts)

    def test_invalid_mode_rejected(self):
        with self.assertRaises(ValueError):
            app.run_report(self.path, mode="fake")


if __name__ == "__main__":
    unittest.main()
