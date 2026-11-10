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
    extract_finish_price,
    format_currency_diff,
    format_console_summary,
    load_iconic_pairs,
    build_tier_summary,
    generate_markdown_report,
    resolve_card_price_unbounded,
    resolve_product_finish,
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
                "color": "Red",
                "cardType": "Legend",
            },
            "102": {
                "productId": 102,
                "name": "Adam Smasher - Ender of Legends",
                "groupName": "Welcome to Night City - Retail",
                "printNumber": "141",
                "rarity": "IL",
                "color": "Red",
                "cardType": "Legend",
            },
            "201": {
                "productId": 201,
                "name": "Johnny Silverhand - Never Stop Fighting",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B159",
                "rarity": "IO",
                "color": "Yellow",
                "cardType": "Unit",
            },
            "202": {
                "productId": 202,
                "name": "Johnny Silverhand - Never Stop Fighting",
                "groupName": "Welcome to Night City - Retail",
                "printNumber": "159",
                "rarity": "IO",
                "color": "Yellow",
                "cardType": "Unit",
            },
            "301": {
                "productId": 301,
                "name": "Judy Alvarez - Nothing to Doubt",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B168",
                "rarity": "IS",
                "color": "Blue",
                "cardType": "Unit",
            },
            "302": {
                "productId": 302,
                "name": "Judy Alvarez - Nothing to Doubt",
                "groupName": "Welcome to Night City - Retail",
                "printNumber": "168",
                "rarity": "IS",
                "color": "Blue",
                "cardType": "Unit",
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

    def test_compute_quantiles_empty_returns_all_none(self):
        self.assertEqual(compute_quantiles([]), (None, None, None, None, None))

    def test_compute_quantiles_under_four_items_returns_none_for_quartiles(self):
        # 1 item
        q_min, q25, med, q75, q_max = compute_quantiles([25.0])
        self.assertEqual((q_min, q25, med, q75, q_max), (25.0, None, 25.0, None, 25.0))

        # 2 items
        q_min, q25, med, q75, q_max = compute_quantiles([10.0, 30.0])
        self.assertEqual((q_min, q25, med, q75, q_max), (10.0, None, 20.0, None, 30.0))

        # 3 items
        q_min, q25, med, q75, q_max = compute_quantiles([10.0, 20.0, 30.0])
        self.assertEqual((q_min, q25, med, q75, q_max), (10.0, None, 20.0, None, 30.0))

    def test_compute_quantiles_four_or_more_items(self):
        vals = [10.0, 20.0, 30.0, 40.0, 50.0]
        q_min, q25, med, q75, q_max = compute_quantiles(vals)
        self.assertEqual(q_min, 10.0)
        self.assertEqual(med, 30.0)
        self.assertEqual(q_max, 50.0)
        self.assertIsNotNone(q25)
        self.assertIsNotNone(q75)
        self.assertTrue(q25 < med < q75)

    def test_extract_finish_price_strictly_checks_preferred_finish(self):
        # Card has only Normal finish, asking for Foil must return None (no silent fallback)
        p_data = {"Normal": {"marketPrice": 15.0}}
        self.assertIsNone(extract_finish_price(p_data, "Foil"))

        # Asking for Standard matches Normal alias
        self.assertEqual(extract_finish_price(p_data, "Standard"), 15.0)

        # Asking for Foil matches Foil
        foil_data = {"Foil": {"marketPrice": 25.0}}
        self.assertEqual(extract_finish_price(foil_data, "Foil"), 25.0)
        self.assertIsNone(extract_finish_price(foil_data, "Standard"))

    def test_resolve_product_finish_recognizes_zero_dollar_key(self):
        latest = {"999": {"Foil": {"marketPrice": 0.0}}}
        finish = resolve_product_finish(999, "Card 999", latest, [], {})
        self.assertEqual(finish, "Foil")

    def test_resolve_product_finish_multiple_finishes_raises(self):
        latest = {"999": {"Foil": {"marketPrice": 10.0}, "Normal": {"marketPrice": 5.0}}}
        with self.assertRaises(ValueError) as ctx:
            resolve_product_finish(999, "Card 999", latest, [], {})
        self.assertIn("Ambiguous multiple finishes for Card 999", str(ctx.exception))
        self.assertIn("productId=999", str(ctx.exception))

    def test_resolve_product_finish_missing_product_raises_fail_closed(self):
        with self.assertRaises(ValueError) as ctx:
            resolve_product_finish(999, "Unpriced Card", {}, [], {})
        self.assertIn("No price records or finish keys found across catalog history for Unpriced Card", str(ctx.exception))
        self.assertIn("productId=999", str(ctx.exception))

    def test_non_foil_derived_finish_accepted_and_mismatch_warned(self):
        # Set card 101 and 102 to have Normal finish in prices
        prices_normal = {
            "date": "2026-11-09",
            "prices": {
                "101": {"Normal": {"marketPrice": 50.0}},
                "102": {"Normal": {"marketPrice": 40.0}},
                "201": {"Foil": {"marketPrice": 30.0}},
                "202": {"Foil": {"marketPrice": 40.0}},
                "301": {"Foil": {"marketPrice": 500.0}},
                "302": {"Foil": {"marketPrice": 400.0}},
            },
        }
        (self.prices_dir / "latest.json").write_text(json.dumps(prices_normal), encoding="utf-8")

        # Collection with card 141 having 1 Standard (should be accepted) and 1 Foil (should be warned/skipped)
        col_file = self.test_dir / "standard_finish_test.csv"
        rows = [
            "name,expansion,printNumber,finish,totalQtyOwned",
            "Adam Smasher,Welcome to Night City - Beta,B141,Standard,2",  # Matching derived finish
            "Adam Smasher,Welcome to Night City - Beta,B141,Foil,1",      # Mismatch (derived is Standard)
        ]
        col_file.write_text("\n".join(rows) + "\n", encoding="utf-8")

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            pairs, _, _ = load_iconic_pairs(
                cards_path=self.cards_file,
                prices_dir=self.prices_dir,
                collection_path=str(col_file),
            )

        p141 = next(p for p in pairs if p.print_number == "141")
        self.assertEqual(p141.valid_finish, "Standard")
        self.assertEqual(p141.beta_owned, 2)
        self.assertIn("Warning: Collection row for Welcome to Night City - Beta #B141 has finish 'Foil' but card is Standard-only. Excluding.", stderr_buf.getvalue())

    def test_rarity_mismatch_between_beta_and_retail_raises(self):
        bad_catalog = dict(self.mock_catalog)
        bad_catalog["101"] = dict(bad_catalog["101"], rarity="IL")
        bad_catalog["102"] = dict(bad_catalog["102"], rarity="IS")  # Mismatched rarity
        bad_file = self.test_dir / "mismatched_rarity.json"
        bad_file.write_text(json.dumps(bad_catalog), encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            load_iconic_pairs(cards_path=bad_file, prices_dir=self.prices_dir)
        self.assertIn("Rarity mismatch between Beta (IL) and Retail (IS)", str(ctx.exception))

    def test_finish_mismatch_between_beta_and_retail_raises(self):
        # Beta is Foil, Retail is Normal
        prices_conflict = {
            "date": "2026-11-09",
            "prices": {
                "101": {"Foil": {"marketPrice": 100.0}},
                "102": {"Normal": {"marketPrice": 80.0}},  # Conflict
                "201": {"Foil": {"marketPrice": 30.0}},
                "202": {"Foil": {"marketPrice": 40.0}},
                "301": {"Foil": {"marketPrice": 500.0}},
                "302": {"Foil": {"marketPrice": 400.0}},
            },
        }
        (self.prices_dir / "latest.json").write_text(json.dumps(prices_conflict), encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            load_iconic_pairs(cards_path=self.cards_file, prices_dir=self.prices_dir)
        self.assertIn("Finish mismatch between Beta (Foil) and Retail (Standard)", str(ctx.exception))

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
        self.assertEqual(p141.valid_finish, "Foil")
        self.assertEqual(p141.beta_price, 100.0)
        self.assertEqual(p141.retail_price, 80.0)
        self.assertEqual(p141.spread, 20.0)
        self.assertAlmostEqual(p141.beta_premium_pct, 25.0)
        self.assertEqual(p141.color, "Red")
        self.assertEqual(p141.card_type, "Legend")
        self.assertEqual(p141.beta_owned, 1)
        self.assertEqual(p141.retail_owned, 1)
        self.assertTrue(p141.pair_complete)

    def test_dynamic_orphan_validation_raises(self):
        bad_catalog = dict(self.mock_catalog)
        del bad_catalog["102"]
        bad_file = self.test_dir / "bad_cards.json"
        bad_file.write_text(json.dumps(bad_catalog), encoding="utf-8")

        with self.assertRaises(ValueError) as ctx:
            load_iconic_pairs(cards_path=bad_file, prices_dir=self.prices_dir)
        self.assertIn("Orphaned Beta print numbers without Retail pair: 141", str(ctx.exception))

    def test_unbounded_price_carry_forward_and_caching(self):
        latest = {"date": "2026-11-09", "prices": {"101": {"Foil": {"marketPrice": 0.0}}}}
        (self.prices_dir / "latest.json").write_text(json.dumps(latest), encoding="utf-8")

        old_snapshot = {"date": "2026-09-25", "prices": {"101": {"Foil": {"marketPrice": 95.50}}}}
        (self.prices_dir / "2026-09-25.json").write_text(json.dumps(old_snapshot), encoding="utf-8")

        hist_files = [self.prices_dir / "2026-09-25.json"]
        cache = {}
        price = resolve_card_price_unbounded(101, "Foil", latest["prices"], hist_files, cache)
        self.assertEqual(price, 95.50)
        self.assertIn(hist_files[0], cache)

    def test_duplicate_collection_rows_sum_quantities(self):
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

    def test_run_iconics_comparison_writes_latest_pointer(self):
        out_md = self.test_dir / "wnc_iconics_comparison.md"
        with patch("tracker.iconics.REPO_ROOT", self.test_dir):
            rc, _ = run_iconics_comparison(
                output_path=str(out_md),
                collection_path=str(self.collection_csv),
                cards_path=self.cards_file,
                prices_dir=self.prices_dir,
            )
        self.assertEqual(rc, 0)
        pointer_file = self.test_dir / ".latest_iconics_report"
        self.assertTrue(pointer_file.is_file())
        self.assertEqual(pointer_file.read_text(encoding="utf-8"), str(out_md.resolve()))

    def test_markdown_report_9_columns_and_all_unpriced_renders_na(self):
        # Create unpriced pairs
        p1 = IconicCardPair("141", "Card 1", "Legends", "Foil", 101, 102, None, None, None, None, 1, 1, color="Red", card_type="Legend")
        t_sums = [build_tier_summary("Legends", [p1])]
        out_md = self.test_dir / "unpriced_output.md"

        generate_markdown_report(
            pairs=[p1],
            tier_summaries=t_sums,
            output_path=str(out_md),
            collection_name="test.csv",
            collection_mtime_date="2026-11-09",
            prices_date="2026-11-09",
        )

        content = out_md.read_text(encoding="utf-8")
        # Metadata header matching portfolio_summary.md
        self.assertIn("# Welcome to Night City: Beta vs. Retail Iconic Cards", content)
        self.assertIn("**Collection Last Updated**: `2026-11-09`", content)
        self.assertIn("**Collection Source**: `test.csv`", content)
        self.assertIn("**Prices Last Updated**: `2026-11-09`", content)
        self.assertIn("**Report Generated**:", content)

        # Ensure exact 9 columns in card table
        self.assertIn("| # | Card Name | Type | Beta Price | Retail Price | Beta Premium ($) | Beta Premium (%) | Beta Owned | Retail Owned |", content)
        self.assertIn("🔴 Card 1", content)

        # Distribution tables render N/A for empty/unpriced tier and All Iconics without crashing
        self.assertIn("### Beta", content)
        self.assertIn("### Retail", content)
        self.assertIn("| Iconic Legends (0/1 pairs) | 1 | N/A | N/A | N/A | N/A | N/A |", content)
        self.assertIn("| **Total Iconics (0/1 pairs)** | **1** | N/A | N/A | N/A | N/A | N/A |", content)

        # Category total row renders N/A with physical copies
        self.assertIn("| | **Total Legends (0/1 pairs)** | | N/A | N/A | N/A | N/A | **1** | **1** |", content)

    def test_markdown_report_paired_subset_set_values_and_label(self):
        # Pair 1: priced on both sides
        p1 = IconicCardPair("141", "Card 1", "Legends", "Foil", 101, 102, 100.0, 80.0, 20.0, 25.0, 1, 0, color="Red", card_type="Legend")
        # Pair 2: Retail price is missing (like Royce #143)
        p2 = IconicCardPair("143", "Card 2", "Legends", "Foil", 103, 104, 58.41, None, None, None, 0, 0, color="Red", card_type="Legend")

        out_incomplete = self.test_dir / "incomplete_set.md"
        t_sums = [build_tier_summary("Legends", [p1, p2])]
        generate_markdown_report(
            pairs=[p1, p2],
            tier_summaries=t_sums,
            output_path=str(out_incomplete),
        )
        content = out_incomplete.read_text(encoding="utf-8")
        # Category label reflects 1/2 pairs in 5-number distributions
        self.assertIn("| Iconic Legends (1/2 pairs) | 1 | `$100.00` | N/A | `$100.00` | N/A | `$100.00` |", content)
        # Category total row sums over paired subset only ($100.00 and $80.00), excluding p2's Beta $58.41
        self.assertIn("| | **Total Legends (1/2 pairs)** | | **`$100.00`** | **`$80.00`** | **`+$20.00`** | **`+25.0%`** | **1** | **0** |", content)

        # When all pairs are priced, label is Complete N cards without '(N/M pairs)'
        out_complete = self.test_dir / "complete_set.md"
        generate_markdown_report(
            pairs=[p1],
            tier_summaries=[build_tier_summary("Legends", [p1])],
            output_path=str(out_complete),
        )
        complete_content = out_complete.read_text(encoding="utf-8")
        self.assertIn("| Iconic Legends (1) | 1 | `$100.00` | N/A | `$100.00` | N/A | `$100.00` |", complete_content)
        self.assertIn("| | **Total Legends (1)** | | **`$100.00`** | **`$80.00`** | **`+$20.00`** | **`+25.0%`** | **1** | **0** |", complete_content)

    def test_console_summary_owned_value_alignment_and_na_width(self):
        # Mixed 1-digit and 2-digit counts (27 Beta cards, 0 Retail cards)
        p1 = IconicCardPair("141", "Card 1", "Legends", "Foil", 101, 102, 10.0, 8.0, 2.0, 25.0, 27, 0)
        # Tier with no prices (N/A medians)
        p_unpriced = IconicCardPair("142", "Card 2", "Others", "Foil", 201, 202, None, None, None, None, 0, 0)

        legends_sum = build_tier_summary("Legends", [p1])
        others_sum = build_tier_summary("Others", [p_unpriced])

        summary = format_console_summary(
            tier_summaries=[legends_sum, others_sum],
            pairs=[p1, p_unpriced],
            output_path="test_report.md",
        )

        lines = summary.split("\n")
        # Check median column alignment: line for Legends (priced) and Others (N/A) must align
        legends_line = next(l for l in lines if l.startswith("Legends (1)"))
        others_line = next(l for l in lines if l.startswith("Others (1)"))
        # Medians start at column 42, retail medians at 54, spread at 66
        self.assertIn("    N/A", others_line)
        self.assertEqual(len(legends_line), len(others_line))

        # Check Owned Collection Value block
        beta_line = next(l for l in lines if l.startswith("  Beta (27 cards):"))
        retail_line = next(l for l in lines if l.startswith("  Retail (0 cards):"))
        total_line = next(l for l in lines if l.startswith("  Total:"))

        # Assert no spaces between $ and digits
        self.assertIn("$270.00", beta_line)
        self.assertNotIn("$ ", beta_line)
        self.assertIn("$0.00", retail_line)
        self.assertNotIn("$ ", retail_line)
        self.assertIn("$270.00", total_line)
        self.assertNotIn("$ ", total_line)

        # Assert all 3 lines terminate at column 33
        self.assertEqual(len(beta_line), 33)
        self.assertEqual(len(retail_line), 33)
        self.assertEqual(len(total_line), 33)


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
            output_report=str(self.test_dir / "portfolio_summary.md"),
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
        mock_run.assert_called_once_with(
            output_path=str(self.test_dir / "wnc_iconics_comparison.md"),
            collection_path=str(self.collection_file),
        )
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
    @patch("run_tracker.run_iconics_comparison")
    def test_cli_catches_value_error_and_exits_1(self, mock_run, mock_cfg):
        mock_cfg.return_value = self.fake_cfg
        mock_run.side_effect = ValueError("Catalog pairing mismatch")

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as ctx:
                run_tracker.main(["compare-iconics"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("Error: Catalog pairing mismatch", stderr_buf.getvalue())

    @patch("run_tracker.load_config")
    @patch("run_tracker.run_iconics_comparison")
    def test_cli_catches_file_not_found_error_and_exits_1(self, mock_run, mock_cfg):
        mock_cfg.return_value = self.fake_cfg
        mock_run.side_effect = FileNotFoundError("Missing collection file")

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as ctx:
                run_tracker.main(["compare-iconics"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("Error: Missing collection file", stderr_buf.getvalue())

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
        mock_run.assert_called_once_with(
            output_path=str(self.test_dir / "wnc_iconics_comparison.md"),
            collection_path=str(self.collection_file),
        )

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
