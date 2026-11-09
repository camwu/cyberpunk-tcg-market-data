"""
Unit and integration tests for WNC Iconic cards comparison engine and CLI subcommand.
"""

import csv
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import run_tracker
from tracker.config import TrackerConfig
from tracker.iconics import (
    compute_quantiles,
    format_currency_diff,
    format_console_summary,
    load_iconic_pairs,
    build_tier_summary,
    generate_markdown_report,
    resolve_card_price_unbounded,
    run_iconics_comparison,
    IconicCardPair,
    TierSummary,
    TIER_MAP,
)
from tracker.validation import CollectionValidationError


class TestIconicsEngine(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

        # Mock catalog with 3 pairs: 1 Legend (141), 1 Other (159), 1 Secret (168)
        self.mock_catalog = {
            "_groups": {
                "1": {"name": "Welcome to Night City - Beta"},
                "2": {"name": "Welcome to Night City - Retail"},
            },
            "101": {
                "productId": 101,
                "name": "Adam Smasher - Ender of Legends",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B141",
                "rarity": "IL",
            },
            "102": {
                "productId": 102,
                "name": "Adam Smasher - Ender of Legends",
                "groupName": "Welcome to Night City - Retail",
                "printNumber": "141",
                "rarity": "IL",
            },
            "201": {
                "productId": 201,
                "name": "Johnny Silverhand - Never Stop Fighting",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B159",
                "rarity": "IO",
            },
            "202": {
                "productId": 202,
                "name": "Johnny Silverhand - Never Stop Fighting",
                "groupName": "Welcome to Night City - Retail",
                "printNumber": "159",
                "rarity": "IO",
            },
            "301": {
                "productId": 301,
                "name": "Judy Alvarez - Nothing to Doubt",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B168",
                "rarity": "IS",
            },
            "302": {
                "productId": 302,
                "name": "Judy Alvarez - Nothing to Doubt",
                "groupName": "Welcome to Night City - Retail",
                "printNumber": "168",
                "rarity": "IS",
            },
        }

        self.cards_file = self.test_dir / "cards.json"
        self.cards_file.write_text(json.dumps(self.mock_catalog), encoding="utf-8")

        self.prices_dir = self.test_dir / "prices"
        self.prices_dir.mkdir(parents=True, exist_ok=True)

        self.latest_prices = {
            "date": "2026-11-09",
            "prices": {
                "101": {"Foil": {"marketPrice": 100.0}},
                "102": {"Foil": {"marketPrice": 80.0}},
                "201": {"Foil": {"marketPrice": 30.0}},
                "202": {"Foil": {"marketPrice": 40.0}},
                "301": {"Foil": {"marketPrice": 500.0}},
                "302": {"Foil": {"marketPrice": 400.0}},
            },
        }
        (self.prices_dir / "latest.json").write_text(json.dumps(self.latest_prices), encoding="utf-8")

        # Mock collection CSV
        self.collection_csv = self.test_dir / "active_collection.csv"
        csv_rows = [
            "name,expansion,printNumber,finish,totalQtyOwned",
            "Adam Smasher,Welcome to Night City - Beta,B141,Foil,1",
            "Johnny Silverhand,Welcome to Night City - Beta,B159,Foil,2",
            "Adam Smasher,Welcome to Night City - Retail,141,Foil,1",
        ]
        self.collection_csv.write_text("\n".join(csv_rows) + "\n", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compute_quantiles(self):
        vals = [10.0, 20.0, 30.0, 40.0, 50.0]
        q_min, q25, med, q75, q_max = compute_quantiles(vals)
        self.assertEqual(q_min, 10.0)
        self.assertEqual(med, 30.0)
        self.assertEqual(q_max, 50.0)
        self.assertTrue(q25 < med < q75)

        # Empty handling
        self.assertEqual(compute_quantiles([]), (0.0, 0.0, 0.0, 0.0, 0.0))

    def test_load_iconic_pairs_success(self):
        pairs, col_name, col_mtime = load_iconic_pairs(
            cards_path=self.cards_file,
            prices_dir=self.prices_dir,
            collection_path=str(self.collection_csv),
        )
        self.assertEqual(len(pairs), 3)
        self.assertEqual(col_name, "active_collection.csv")
        self.assertIsNotNone(col_mtime)

        # Check pair 141 (Legends)
        p141 = next(p for p in pairs if p.print_number == "141")
        self.assertEqual(p141.tier, "Legends")
        self.assertEqual(p141.beta_price, 100.0)
        self.assertEqual(p141.retail_price, 80.0)
        self.assertEqual(p141.spread, 20.0)
        self.assertAlmostEqual(p141.ratio, 1.25)
        self.assertEqual(p141.beta_owned, 1)
        self.assertEqual(p141.retail_owned, 1)
        self.assertTrue(p141.pair_complete)

        # Check pair 159 (Others)
        p159 = next(p for p in pairs if p.print_number == "159")
        self.assertEqual(p159.tier, "Others")
        self.assertEqual(p159.spread, -10.0)
        self.assertAlmostEqual(p159.ratio, 0.75)
        self.assertEqual(p159.beta_owned, 2)
        self.assertEqual(p159.retail_owned, 0)
        self.assertFalse(p159.pair_complete)

    def test_dynamic_orphan_validation_raises(self):
        # Remove retail card 141 from catalog
        bad_catalog = dict(self.mock_catalog)
        del bad_catalog["102"]
        bad_file = self.test_dir / "bad_cards.json"
        bad_file.write_text(json.dumps(bad_catalog), encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            load_iconic_pairs(cards_path=bad_file, prices_dir=self.prices_dir)
        self.assertIn("Orphaned Beta print numbers without Retail pair: 141", str(ctx.exception))

    def test_zero_iconic_cards_raises(self):
        empty_catalog = {"_groups": {}}
        bad_file = self.test_dir / "empty_cards.json"
        bad_file.write_text(json.dumps(empty_catalog), encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            load_iconic_pairs(cards_path=bad_file, prices_dir=self.prices_dir)
        self.assertIn("No Welcome to Night City Iconic cards found in catalog", str(ctx.exception))

    def test_unbounded_price_carry_forward_and_caching(self):
        # Card 101 missing from latest.json, available 45 days ago in 2026-09-25.json
        latest = {"date": "2026-11-09", "prices": {"101": {"Foil": {"marketPrice": 0.0}}}}
        (self.prices_dir / "latest.json").write_text(json.dumps(latest), encoding="utf-8")

        old_snapshot = {"date": "2026-09-25", "prices": {"101": {"Foil": {"marketPrice": 95.50}}}}
        (self.prices_dir / "2026-09-25.json").write_text(json.dumps(old_snapshot), encoding="utf-8")

        hist_files = [self.prices_dir / "2026-09-25.json"]
        cache = {}
        price = resolve_card_price_unbounded(101, "Foil", latest["prices"], hist_files, cache)
        self.assertEqual(price, 95.50)
        self.assertIn(hist_files[0], cache)

    def test_permanently_unpriced_card_returns_none(self):
        latest = {"date": "2026-11-09", "prices": {}}
        hist_files = []
        cache = {}
        price = resolve_card_price_unbounded(999, "Foil", latest["prices"], hist_files, cache)
        self.assertIsNone(price)

    def test_paired_subset_medians_invariant(self):
        # Create pairs where one pair has Retail = None
        p1 = IconicCardPair("141", "Card 1", "Legends", "Foil", 1, 2, 100.0, 50.0, 50.0, 2.0, 1, 0)
        p2 = IconicCardPair("142", "Card 2", "Legends", "Foil", 3, 4, 80.0, None, None, None, 1, 0)

        summary = build_tier_summary("Legends", [p1, p2])
        self.assertEqual(summary.total_cards, 2)
        self.assertEqual(summary.priced_pairs_count, 1)
        # Because p2 has None retail price, neither p2.beta_price nor p2.retail_price enters medians
        self.assertEqual(summary.beta_median, 100.0)
        self.assertEqual(summary.retail_median, 50.0)
        self.assertEqual(summary.spread, 50.0)

    def test_duplicate_collection_rows_sum_quantities(self):
        # CSV with two lots for Beta B141 (qty 2 and qty 3 -> total 5)
        col_file = self.test_dir / "multi_lot.csv"
        rows = [
            "name,expansion,printNumber,finish,totalQtyOwned",
            "Adam Smasher,Welcome to Night City - Beta,B141,Foil,2",
            "Adam Smasher,Welcome to Night City - Beta,B141,Foil,3",
            "Adam Smasher,Welcome to Night City - Retail,141,Foil,1",
        ]
        col_file.write_text("\n".join(rows) + "\n", encoding="utf-8")

        pairs, _, _ = load_iconic_pairs(
            cards_path=self.cards_file,
            prices_dir=self.prices_dir,
            collection_path=str(col_file),
        )
        p141 = next(p for p in pairs if p.print_number == "141")
        self.assertEqual(p141.beta_owned, 5)

    def test_collection_finish_mismatch_warning(self):
        col_file = self.test_dir / "mismatch.csv"
        rows = [
            "name,expansion,printNumber,finish,totalQtyOwned",
            "Adam Smasher,Welcome to Night City - Beta,B141,Standard,2",  # Standard instead of Foil
        ]
        col_file.write_text("\n".join(rows) + "\n", encoding="utf-8")

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            pairs, _, _ = load_iconic_pairs(
                cards_path=self.cards_file,
                prices_dir=self.prices_dir,
                collection_path=str(col_file),
            )
        self.assertIn("Warning: Collection row for Welcome to Night City - Beta #B141 has finish 'Standard'", stderr_buf.getvalue())
        p141 = next(p for p in pairs if p.print_number == "141")
        self.assertEqual(p141.beta_owned, 0)  # Excluded due to finish mismatch

    def test_zero_wnc_rows_warning(self):
        col_file = self.test_dir / "other_set.csv"
        rows = [
            "name,expansion,printNumber,finish,totalQtyOwned",
            "Johnny Silverhand,Edgerunner Open Season 1,001,Foil,1",
        ]
        col_file.write_text("\n".join(rows) + "\n", encoding="utf-8")

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            load_iconic_pairs(
                cards_path=self.cards_file,
                prices_dir=self.prices_dir,
                collection_path=str(col_file),
            )
        self.assertIn("zero rows matched 'Welcome to Night City - Beta' or 'Welcome to Night City - Retail'", stderr_buf.getvalue())

    def test_console_summary_width_and_staleness(self):
        pairs, col_name, col_mtime = load_iconic_pairs(
            cards_path=self.cards_file,
            prices_dir=self.prices_dir,
            collection_path=str(self.collection_csv),
        )
        legends_sum = build_tier_summary("Legends", [p for p in pairs if p.tier == "Legends"])
        others_sum = build_tier_summary("Others", [p for p in pairs if p.tier == "Others"])
        secrets_sum = build_tier_summary("Secrets", [p for p in pairs if p.tier == "Secrets"])

        summary = format_console_summary(
            tier_summaries=[legends_sum, others_sum, secrets_sum],
            pairs=pairs,
            output_path="test_report.md",
            collection_name=col_name,
            collection_mtime_date=col_mtime,
        )

        lines = summary.split("\n")
        for idx, line in enumerate(lines):
            self.assertLessEqual(
                len(line),
                76,
                f"Line {idx} exceeds 76 columns ({len(line)} chars): '{line}'",
            )
        self.assertIn("Collection: active_collection.csv", summary)
        self.assertIn("Legends (1)", summary)

    def test_markdown_report_generation(self):
        pairs, col_name, col_mtime = load_iconic_pairs(
            cards_path=self.cards_file,
            prices_dir=self.prices_dir,
            collection_path=str(self.collection_csv),
        )
        t_sums = [build_tier_summary("Legends", [p for p in pairs if p.tier == "Legends"])]
        out_md = self.test_dir / "output.md"

        generate_markdown_report(
            pairs=pairs,
            tier_summaries=t_sums,
            output_path=str(out_md),
            collection_name=col_name,
            collection_mtime_date=col_mtime,
        )

        self.assertTrue(out_md.is_file())
        content = out_md.read_text(encoding="utf-8")
        self.assertIn("# Welcome to Night City: Beta vs. Retail Iconic Cards", content)
        self.assertIn("Per-Card Price Spread Distribution", content)
        self.assertIn("| 141 | Adam Smasher - Ender of Legends |", content)


class TestIconicsCLI(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.collection_file = self.test_dir / "active_collection.csv"
        self.collection_file.write_text("name,expansion,printNumber,finish,totalQtyOwned\n", encoding="utf-8")

        self.fake_cfg = TrackerConfig(
            collection_csv=str(self.collection_file),
            database_path=str(self.test_dir / "test.db"),
            price_cache_dir=str(self.test_dir / "prices"),
            output_report=str(self.test_dir / "LATEST_PORTFOLIO_SUMMARY.md"),
            cardnexus_api_key="test_api_key",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("run_tracker.load_config")
    @patch("run_tracker.run_iconics_comparison")
    def test_cli_subcommand_dispatch_success(self, mock_run, mock_cfg):
        mock_cfg.return_value = self.fake_cfg
        mock_run.return_value = (0, "MOCK_CONSOLE_SUMMARY")

        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf):
            with self.assertRaises(SystemExit) as ctx:
                run_tracker.main(["compare-iconics"])

        self.assertEqual(ctx.exception.code, 0)
        mock_run.assert_called_once()
        self.assertIn("MOCK_CONSOLE_SUMMARY", stdout_buf.getvalue())

    @patch("run_tracker.load_config")
    def test_cli_rejects_sync_and_collection_together(self, mock_cfg):
        mock_cfg.return_value = self.fake_cfg
        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as ctx:
                run_tracker.main(["compare-iconics", "--sync-collection", "--collection", "custom.csv"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("Cannot specify --sync-collection together with an explicit --collection path", stderr_buf.getvalue())

    @patch("run_tracker.load_config")
    def test_cli_missing_collection_file_exits_1(self, mock_cfg):
        empty_dir = self.test_dir / "empty_dir"
        empty_dir.mkdir(parents=True, exist_ok=True)
        self.fake_cfg.collection_csv = str(empty_dir / "missing.csv")
        mock_cfg.return_value = self.fake_cfg

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as ctx:
                run_tracker.main(["compare-iconics"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("Run with --sync-collection to download active collection", stderr_buf.getvalue())

    @patch("run_tracker.load_config")
    @patch("run_tracker.sync_cardnexus_collection")
    @patch("run_tracker.run_iconics_comparison")
    def test_cli_sync_collection_success(self, mock_run, mock_sync, mock_cfg):
        mock_cfg.return_value = self.fake_cfg
        mock_sync.return_value = (True, str(self.collection_file), 10)
        mock_run.return_value = (0, "SUMMARY")

        with patch("sys.stdout", io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                run_tracker.main(["compare-iconics", "--sync-collection"])
        self.assertEqual(ctx.exception.code, 0)
        mock_sync.assert_called_once()
        mock_run.assert_called_once()

    @patch("run_tracker.load_config")
    @patch("run_tracker.sync_cardnexus_collection")
    def test_cli_sync_collection_failure_exits_1(self, mock_sync, mock_cfg):
        mock_cfg.return_value = self.fake_cfg
        mock_sync.return_value = (False, "", 0)

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as ctx:
                run_tracker.main(["compare-iconics", "--sync-collection"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("CardNexus collection synchronization failed", stderr_buf.getvalue())

    @patch("run_tracker.load_config")
    @patch("run_tracker.sync_cardnexus_collection")
    def test_cli_sync_collection_validation_error_exits_1(self, mock_sync, mock_cfg):
        mock_cfg.return_value = self.fake_cfg
        mock_sync.side_effect = CollectionValidationError("Schema corrupt")

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as ctx:
                run_tracker.main(["compare-iconics", "--sync-collection"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("CardNexus collection validation failed", stderr_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
