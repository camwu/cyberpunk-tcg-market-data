"""
Unit and integration tests for WNC Iconic cards comparison engine and CLI subcommand.
"""

import csv
import io
import json
import os
from pathlib import Path
import sqlite3
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
    resolve_card_price_unbounded,
    resolve_product_finish,
    run_iconics_comparison,
    IconicCardPair,
    TierSummary,
    TIER_MAP,
)
from tracker.iconics_html import (
    build_edition_trajectories,
    generate_html_dashboard,
    load_collection_acquisitions,
    serialize_json_for_script,
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
        out_html = self.test_dir / "wnc_iconic_price_trajectories.html"
        with patch("tracker.iconics.REPO_ROOT", self.test_dir):
            rc, _ = run_iconics_comparison(
                output_path=str(out_html),
                collection_path=str(self.collection_csv),
                cards_path=self.cards_file,
                prices_dir=self.prices_dir,
            )
        self.assertEqual(rc, 0)
        self.assertTrue(out_html.is_file())
        pointer_file = self.test_dir / ".latest_iconics_report"
        self.assertTrue(pointer_file.is_file())
        self.assertEqual(pointer_file.read_text(encoding="utf-8"), str(out_html.resolve()))

    def test_html_dashboard_all_unpriced_renders_na_and_self_contained(self):
        p1 = IconicCardPair("141", "Card 1", "Legends", "Foil", 101, 102, None, None, None, None, 1, 1, color="Red", card_type="Legend")
        out_html = self.test_dir / "unpriced_output.html"
        empty_prices = self.test_dir / "empty_prices"
        empty_prices.mkdir(parents=True, exist_ok=True)
        (empty_prices / "2026-11-09.json").write_text(json.dumps({"date": "2026-11-09", "prices": {}}), encoding="utf-8")

        generate_html_dashboard(
            pairs=[p1],
            prices_dir=empty_prices,
            collection_path=None,
            output_path=str(out_html),
            database_path=None,
            earliest_beta_date=None,
            earliest_retail_date=None,
        )

        content = out_html.read_text(encoding="utf-8")
        self.assertIn("Welcome to Night City &ndash; Iconic Price Trajectories", content)
        self.assertIn("Priced Set Value (0/1 pairs)", content)
        self.assertIn("Total (0/1 pairs)", content)
        self.assertIn('<span id="dist-b-med" class="comp-distro-med-val">N/A</span>', content)
        self.assertIn("Card 1", content)
        # Strictly self-contained assets: no external scripts or stylesheets
        self.assertNotIn("<script src=", content)
        self.assertNotIn('<link rel="stylesheet"', content)

    def test_html_dashboard_paired_subset_labels_and_totals(self):
        # Pair 1: priced on both sides; Pair 2: Retail unpriced
        p1 = IconicCardPair("141", "Card 1", "Legends", "Foil", 101, 102, 100.0, 80.0, 20.0, 25.0, 1, 0, color="Red", card_type="Legend")
        p2 = IconicCardPair("143", "Card 2", "Legends", "Foil", 103, 104, 58.41, None, None, None, 0, 0, color="Red", card_type="Legend")

        p_dir = self.test_dir / "paired_prices"
        p_dir.mkdir(parents=True, exist_ok=True)
        (p_dir / "2026-11-08.json").write_text(json.dumps({
            "date": "2026-11-08",
            "prices": {
                "101": {"Foil": {"marketPrice": 90.0}},
                "102": {"Foil": {"marketPrice": 75.0}},
                "103": {"Foil": {"marketPrice": 58.41}},
            },
        }), encoding="utf-8")
        (p_dir / "2026-11-09.json").write_text(json.dumps({
            "date": "2026-11-09",
            "prices": {
                "101": {"Foil": {"marketPrice": 100.0}},
                "102": {"Foil": {"marketPrice": 80.0}},
                "103": {"Foil": {"marketPrice": 58.41}},
            },
        }), encoding="utf-8")

        out_incomplete = self.test_dir / "incomplete_set.html"
        generate_html_dashboard(
            pairs=[p1, p2],
            prices_dir=p_dir,
            collection_path=None,
            output_path=str(out_incomplete),
            database_path=None,
            earliest_beta_date=None,
            earliest_retail_date=None,
        )
        content = out_incomplete.read_text(encoding="utf-8")
        # Paired subset sums strictly over p1 ($100.00 / $80.00), excluding unpaired $58.41
        self.assertIn("Priced Set Value (1/2 pairs)", content)
        self.assertIn('<span style="color:#58a6ff;" id="set-val-beta">$100.00</span>', content)
        self.assertIn('<span style="color:#8b949e;" id="set-val-retail">$80.00</span>', content)
        self.assertIn('id="total-label-IL">Total (1/2 pairs)</td>', content)
        self.assertIn('id="total-b-price-IL">$100.00</td>', content)
        self.assertIn('id="dist-b-med" class="comp-distro-med-val">$100.00</span>', content)
        self.assertIn('id="dist-r-med" class="comp-distro-med-val">$80.00</span>', content)

        # Complete paired set omits '(N/M pairs)' suffix
        out_complete = self.test_dir / "complete_set.html"
        generate_html_dashboard(
            pairs=[p1],
            prices_dir=p_dir,
            collection_path=None,
            output_path=str(out_complete),
            database_path=None,
            earliest_beta_date=None,
            earliest_retail_date=None,
        )
        complete_content = out_complete.read_text(encoding="utf-8")
        self.assertIn('<div class="comp-card-title" id="set-val-title">Priced Set Value</div>', complete_content)
        self.assertIn('id="total-label-IL">Total</td>', complete_content)
        complete_html_body = complete_content.split("<script>")[0]
        self.assertNotIn("pairs)", complete_html_body)

    def test_trajectory_zero_price_carry_forward_and_delayed_baseline(self):
        p1 = IconicCardPair("141", "Card 1", "Legends", "Foil", 101, 102, 50.0, 40.0, 10.0, 25.0, 0, 0)
        price_by_date = {
            "2026-09-13": {"101": {"Foil": {"marketPrice": 0.0}}},
            "2026-09-14": {"101": {"Foil": {"marketPrice": 40.0}}},
            "2026-09-15": {"101": {"Foil": {"marketPrice": 0.0}}},
            "2026-09-16": {},
            "2026-09-17": {"101": {"Foil": {"marketPrice": 50.0}}},
        }
        trajs = build_edition_trajectories(
            pairs=[p1],
            edition="beta",
            dates=["2026-09-13", "2026-09-14", "2026-09-15", "2026-09-16", "2026-09-17"],
            price_by_date=price_by_date,
            acquisitions={},
        )
        self.assertEqual(len(trajs), 1)
        t = trajs[0]
        # Baseline starts on first non-zero date (2026-09-14) at $40.00, skipping $0.00 on 09-13
        self.assertEqual(t["baselineDate"], "2026-09-14")
        self.assertEqual(t["baselinePrice"], 40.0)
        # Missing/$0.00 scrapes on 09-15 and 09-16 carry forward $40.00
        self.assertEqual(
            t["points"],
            [
                ("2026-09-14", 40.0),
                ("2026-09-15", 40.0),
                ("2026-09-16", 40.0),
                ("2026-09-17", 50.0),
            ],
        )
        self.assertAlmostEqual(t["changeAll"], 10.0)
        self.assertAlmostEqual(t["changeAllPct"], 25.0)

    def test_prehistory_acquisition_clamped_and_svg_rendered(self):
        p1 = IconicCardPair("141", "Card 1", "Legends", "Foil", 101, 102, 60.0, 45.0, 15.0, 33.3, 1, 0, color="Red", card_type="Legend")
        p_dir = self.test_dir / "clamp_prices"
        p_dir.mkdir(parents=True, exist_ok=True)
        (p_dir / "2026-09-13.json").write_text(json.dumps({
            "date": "2026-09-13",
            "prices": {"101": {"Foil": {"marketPrice": 40.0}}, "102": {"Foil": {"marketPrice": 35.0}}},
        }), encoding="utf-8")
        (p_dir / "2026-09-14.json").write_text(json.dumps({
            "date": "2026-09-14",
            "prices": {"101": {"Foil": {"marketPrice": 60.0}}, "102": {"Foil": {"marketPrice": 45.0}}},
        }), encoding="utf-8")

        col_csv = self.test_dir / "early_acq.csv"
        col_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\n"
            "Card 1,Welcome to Night City - Beta,B141,Foil,1,2026-09-02\n",
            encoding="utf-8",
        )
        out_html = self.test_dir / "clamped.html"
        generate_html_dashboard(
            pairs=[p1],
            prices_dir=p_dir,
            collection_path=str(col_csv),
            output_path=str(out_html),
            database_path=None,
            earliest_beta_date="2026-09-13",
            earliest_retail_date=None,
        )
        content = out_html.read_text(encoding="utf-8")
        self.assertIn("<svg viewBox=", content)
        self.assertIn("<polyline class=\"chart-line\"", content)
        # Pre-history 2026-09-02 clamped to 2026-09-13 with baseline $40.00
        self.assertIn("09/13/26", content)
        self.assertIn('data-acq="true" data-acq-cost="$40.00"', content)
        self.assertIn('<span class="price-sub-val">$40.00</span>', content)

    def test_lot_sum_mismatch_raises_collection_validation_error(self):
        bad_csv = self.test_dir / "bad_lots.csv"
        bad_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\n"
            "Adam Smasher,Welcome to Night City - Beta,B141,Foil,2,2026-09-15: 5\n",
            encoding="utf-8",
        )
        with self.assertRaises(CollectionValidationError):
            load_iconic_pairs(
                cards_path=self.cards_file,
                prices_dir=self.prices_dir,
                collection_path=str(bad_csv),
            )
        with self.assertRaises(CollectionValidationError):
            load_collection_acquisitions(
                collection_path=str(bad_csv),
                database_path=None,
                earliest_beta_date=None,
                earliest_retail_date=None,
            )

    def test_acquisitions_keyed_by_finish_separates_standard_and_foil(self):
        p1 = IconicCardPair("141", "Card 1", "Legends", "Foil", 101, 102, 80.0, 60.0, 20.0, 33.3, 1, 0)
        csv_path = self.test_dir / "mixed_finishes.csv"
        csv_path.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\n"
            "Card 1,Welcome to Night City - Beta,B141,Standard,3,2026-09-13\n"
            "Card 1,Welcome to Night City - Beta,B141,Foil,1,2026-09-14\n",
            encoding="utf-8",
        )
        acqs = load_collection_acquisitions(
            collection_path=str(csv_path),
            database_path=None,
            earliest_beta_date="2026-09-13",
            earliest_retail_date=None,
        )
        self.assertEqual(acqs[("Welcome to Night City - Beta", "B141", "Standard")]["date"], "2026-09-13")
        self.assertEqual(acqs[("Welcome to Night City - Beta", "B141", "Foil")]["date"], "2026-09-14")

        price_by_date = {
            "2026-09-13": {"101": {"Foil": {"marketPrice": 40.0}}},
            "2026-09-14": {"101": {"Foil": {"marketPrice": 55.0}}},
            "2026-09-15": {"101": {"Foil": {"marketPrice": 80.0}}},
        }
        trajs = build_edition_trajectories(
            pairs=[p1],
            edition="beta",
            dates=["2026-09-13", "2026-09-14", "2026-09-15"],
            price_by_date=price_by_date,
            acquisitions=acqs,
        )
        # Must select Foil acquisition (2026-09-14 -> $55.00), not merged Standard lot (2026-09-13)
        self.assertEqual(trajs[0]["acqDate"], "2026-09-14")
        self.assertAlmostEqual(trajs[0]["acqCost"], 55.0)

    def test_7d_change_uses_calendar_days_and_undated_owned_has_qty_attr(self):
        p1 = IconicCardPair("141", "Card 1", "Legends", "Foil", 101, 102, 75.0, 60.0, 15.0, 25.0, 2, 0, color="Red", card_type="Legend")
        p_dir = self.test_dir / "gap_prices"
        p_dir.mkdir(parents=True, exist_ok=True)
        for d, val in [("2026-11-01", 50.0), ("2026-11-06", 60.0), ("2026-11-11", 75.0)]:
            (p_dir / f"{d}.json").write_text(json.dumps({
                "date": d,
                "prices": {"101": {"Foil": {"marketPrice": val}}, "102": {"Foil": {"marketPrice": val - 10}}},
            }), encoding="utf-8")

        # Owned quantity=2 with blank notes -> no acquisition date, but still owned!
        undated_csv = self.test_dir / "undated_owned.csv"
        undated_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\n"
            "Card 1,Welcome to Night City - Beta,B141,Foil,2,\n",
            encoding="utf-8",
        )
        out_html = self.test_dir / "gap_owned.html"
        generate_html_dashboard(
            pairs=[p1],
            prices_dir=p_dir,
            collection_path=str(undated_csv),
            output_path=str(out_html),
            database_path=None,
            earliest_beta_date=None,
            earliest_retail_date=None,
        )
        content = out_html.read_text(encoding="utf-8")
        # 7 calendar days before 2026-11-11 is 2026-11-04 -> carried-forward $50.00 (delta +$25.00 / +50.0%)
        self.assertIn("+$25.00 (+50.0%)", content)
        self.assertIn('data-has-acq="0" data-cacq="" data-owned-qty="2"', content)
        self.assertIn("tile.dataset.ownedQty", content)
        self.assertIn("compareNullableNums", content)

    def test_sqlite_baseline_override_and_error_warning(self):
        p1 = IconicCardPair("141", "Card 1", "Legends", "Foil", 101, 102, 80.0, 60.0, 20.0, 33.3, 1, 0, color="Red", card_type="Legend")
        p_dir = self.test_dir / "db_prices"
        p_dir.mkdir(parents=True, exist_ok=True)
        (p_dir / "2026-11-09.json").write_text(json.dumps({
            "date": "2026-11-09",
            "prices": {"101": {"Foil": {"marketPrice": 80.0}}, "102": {"Foil": {"marketPrice": 60.0}}},
        }), encoding="utf-8")

        col_csv = self.test_dir / "db_col.csv"
        col_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\n"
            "Card 1,Welcome to Night City - Beta,B141,Foil,1,2026-11-09\n",
            encoding="utf-8",
        )
        db_path = self.test_dir / "test_meta.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE card_metadata (expansion TEXT, print_number TEXT, finish TEXT, first_seen_date TEXT, baseline_market_price REAL)"
        )
        conn.execute(
            "INSERT INTO card_metadata VALUES (?, ?, ?, ?, ?)",
            ("Welcome to Night City - Beta", "B141", "Foil", "2026-11-09", 45.25),
        )
        conn.commit()
        conn.close()

        out_html = self.test_dir / "db_out.html"
        generate_html_dashboard(
            pairs=[p1],
            prices_dir=p_dir,
            collection_path=str(col_csv),
            output_path=str(out_html),
            database_path=str(db_path),
            earliest_beta_date=None,
            earliest_retail_date=None,
        )
        content = out_html.read_text(encoding="utf-8")
        self.assertIn("$45.25", content)

        # Unreadable/corrupt SQLite DB logs warning to stderr instead of failing silently
        bad_db = self.test_dir / "corrupt.db"
        bad_db.write_text("not a sqlite database", encoding="utf-8")
        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            load_collection_acquisitions(
                collection_path=str(col_csv),
                database_path=str(bad_db),
                earliest_beta_date=None,
                earliest_retail_date=None,
            )
        self.assertIn("Warning: Could not read card_metadata", stderr_buf.getvalue())

    def test_inline_script_json_and_html_escaping(self):
        malicious = '</script><img src=x onerror=alert(1)>'
        escaped_json = serialize_json_for_script({"name": malicious})
        self.assertNotIn("</script>", escaped_json)
        self.assertIn("\\u003c/script\\u003e", escaped_json)

        p1 = IconicCardPair("141", malicious, "Legends", "Foil", 101, 102, 10.0, 8.0, 2.0, 25.0, 0, 0, color="Red", card_type="Legend")
        out_html = self.test_dir / "escaped.html"
        generate_html_dashboard(
            pairs=[p1],
            prices_dir=self.prices_dir,
            collection_path=None,
            output_path=str(out_html),
            database_path=None,
            earliest_beta_date=None,
            earliest_retail_date=None,
        )
        content = out_html.read_text(encoding="utf-8")
        self.assertNotIn("<img src=x onerror=alert(1)>", content)
        self.assertIn("&lt;/script&gt;&lt;img src=x onerror=alert(1)&gt;", content)

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
            output_path="wnc_iconic_price_trajectories.html",
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
            output_path=str(self.test_dir / "wnc_iconic_price_trajectories.html"),
            collection_path=str(self.collection_file),
            database_path=str(self.test_dir / "test.db"),
        )
        self.assertIn("MOCK_CONSOLE_SUMMARY", stdout_buf.getvalue())

    @patch("run_tracker.load_config")
    @patch("run_tracker.resolve_and_sync_collection")
    @patch("run_tracker.run_iconics_comparison")
    def test_cli_default_adapter_passes_none_for_cache_reuse(self, mock_run, mock_resolve, mock_cfg):
        mock_cfg.return_value = self.fake_cfg
        mock_resolve.return_value = run_tracker.CollectionSyncResult(
            collection_path=str(self.collection_file),
            collection_source="CardNexus API (cached)",
            collection_updated=False,
        )
        mock_run.return_value = (0, "OK")

        with patch("sys.stdout", io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                run_tracker.main(["compare-iconics"])

        self.assertEqual(ctx.exception.code, 0)
        mock_resolve.assert_called_once()
        sync_args, resolved_cfg = mock_resolve.call_args[0]
        self.assertIsNone(sync_args.sync_collection)
        self.assertIsNone(sync_args.import_path)
        self.assertEqual(resolved_cfg, self.fake_cfg)

    @patch("run_tracker.load_config")
    @patch("run_tracker.resolve_and_sync_collection")
    @patch("run_tracker.run_iconics_comparison")
    def test_cli_sync_flag_adapter_passes_true_to_force_sync(self, mock_run, mock_resolve, mock_cfg):
        mock_cfg.return_value = self.fake_cfg
        mock_resolve.return_value = run_tracker.CollectionSyncResult(
            collection_path=str(self.collection_file),
            collection_source="CardNexus API",
            collection_updated=True,
        )
        mock_run.return_value = (0, "OK")

        with patch("sys.stdout", io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                run_tracker.main(["compare-iconics", "--sync-collection"])

        self.assertEqual(ctx.exception.code, 0)
        mock_resolve.assert_called_once()
        sync_args, resolved_cfg = mock_resolve.call_args[0]
        self.assertIs(sync_args.sync_collection, True)
        self.assertEqual(resolved_cfg, self.fake_cfg)

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
        self.fake_cfg.cardnexus_api_key = None
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
    @patch("run_tracker.run_iconics_comparison")
    def test_cli_catches_collection_validation_error_and_exits_1(self, mock_run, mock_cfg):
        mock_cfg.return_value = self.fake_cfg
        mock_run.side_effect = CollectionValidationError("Lot mismatch")

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as ctx:
                run_tracker.main(["compare-iconics"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("Error: Lot mismatch", stderr_buf.getvalue())

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
            output_path=str(self.test_dir / "wnc_iconic_price_trajectories.html"),
            collection_path=str(self.collection_file),
            database_path=str(self.test_dir / "test.db"),
        )

    @patch("run_tracker.load_config")
    @patch("run_tracker.sync_cardnexus_collection")
    def test_cli_sync_collection_failure_without_cache_exits_1(self, mock_sync, mock_cfg):
        self.collection_file.unlink()
        mock_cfg.return_value = self.fake_cfg
        mock_sync.return_value = (False, "", 0)

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as ctx:
                run_tracker.main(["compare-iconics", "--sync-collection"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("CardNexus API sync failed", stderr_buf.getvalue())

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
