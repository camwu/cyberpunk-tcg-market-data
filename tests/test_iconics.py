"""
Unit and integration tests for WNC Iconic cards comparison engine and CLI subcommand.
"""

import csv
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import run_tracker
from tracker.config import TrackerConfig
from tracker.iconics import (
    _load_catalog_and_price_context,
    compute_quantiles,
    extract_finish_price,
    format_currency_diff,
    format_console_summary,
    load_iconic_pairs,
    load_ncb_cards,
    build_tier_summary,
    resolve_card_price_unbounded,
    resolve_product_finish,
    run_iconics_comparison,
    IconicCardPair,
    PromoSetCard,
    TierSummary,
    TIER_MAP,
)
from tracker.iconics_common import load_validated_collection_rows
from tracker.iconics_html import (
    _SET_STATE_JS,
    _build_card_trajectory,
    _render_card_tile,
    build_edition_trajectories,
    build_ncb_trajectories,
    generate_html_dashboard,
    generate_svg_chart,
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
        self.assertIn("=============== Welcome to Night City: Iconics & Box Toppers ===============", summary)
        self.assertIn("Collection: active_collection.csv", summary)

    def test_run_iconics_comparison_writes_latest_pointer(self):
        out_html = self.test_dir / "set_price_trajectories.html"
        with patch("tracker.iconics.REPO_ROOT", self.test_dir):
            rc, _ = run_iconics_comparison(
                output_path=str(out_html),
                collection_path=str(self.collection_csv),
                cards_path=self.cards_file,
                prices_dir=self.prices_dir,
            )
        self.assertEqual(rc, 0)
        self.assertTrue(out_html.is_file())
        pointer_file = self.test_dir / ".latest_sets_report"
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
        self.assertIn("Welcome to Night City &ndash; Iconic &amp; Promo Price Trajectories", content)
        self.assertIn("WNC Set Value (0/1 pairs)", content)
        self.assertIn("Total (0/1 pairs)", content)
        self.assertIn('<span id="dist-b-med" class="comp-distro-med-val">N/A</span>', content)
        self.assertIn("Card 1", content)
        self.assertIn('id="chart-legend"', content)
        self.assertIn(">Up</span>", content)
        self.assertIn(">Down</span>", content)
        self.assertIn(">Flat (0%)</span>", content)
        self.assertIn("Acquisition date", content)
        self.assertIn("Current TCG Low (excl. shipping)", content)
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
        self.assertIn("WNC Set Value (1/2 pairs)", content)
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
        self.assertIn('<div class="comp-card-title" id="set-val-title">WNC Set Value</div>', complete_content)
        self.assertIn('<div class="comp-card-title">Owned WNC Value</div>', complete_content)
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
            output_path="set_price_trajectories.html",
        )

        lines = summary.split("\n")
        # Check median column alignment: line for Legends (priced) and Others (N/A) must align
        legends_line = next(l for l in lines if l.startswith("Legends (1)"))
        others_line = next(l for l in lines if l.startswith("Others (1)"))
        # Medians start at column 42, retail medians at 54, spread at 66
        self.assertIn("    N/A", others_line)
        self.assertEqual(len(legends_line), len(others_line))

        # Check Owned WNC Value block
        self.assertIn("Owned WNC Value:", summary)
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

    def test_sqlite_multilot_weighted_cost_and_updated_csv_date_not_clobbered(self):
        p1 = IconicCardPair("141", "Card 1", "Legends", "Foil", 101, 102, 100.0, 80.0, 20.0, 25.0, 3, 0, color="Red", card_type="Legend")
        p2 = IconicCardPair("142", "Card 2", "Legends", "Foil", 103, 104, 120.0, 90.0, 30.0, 33.3, 1, 0, color="Blue", card_type="Legend")
        p_dir = self.test_dir / "multilot_prices"
        p_dir.mkdir(parents=True, exist_ok=True)
        for d, v1, v2 in [("2026-09-14", 40.0, 60.0), ("2026-09-20", 70.0, 90.0), ("2026-10-01", 85.0, 95.0), ("2026-11-09", 100.0, 120.0)]:
            (p_dir / f"{d}.json").write_text(json.dumps({
                "date": d,
                "prices": {
                    "101": {"Foil": {"marketPrice": v1}},
                    "102": {"Foil": {"marketPrice": v1 - 5}},
                    "103": {"Foil": {"marketPrice": v2}},
                    "104": {"Foil": {"marketPrice": v2 - 5}},
                },
            }), encoding="utf-8")

        col_csv = self.test_dir / "multilot.csv"
        col_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\n"
            "Card 1,Welcome to Night City - Beta,B141,Foil,3,2026-09-14: 2; 2026-09-20: 1\n"
            "Card 2,Welcome to Night City - Beta,B142,Foil,1,2026-10-01\n",
            encoding="utf-8",
        )
        db_path = self.test_dir / "multilot_meta.db"
        conn = sqlite3.connect(db_path)
        conn.execute(
            "CREATE TABLE card_metadata (expansion TEXT, print_number TEXT, finish TEXT, first_seen_date TEXT, baseline_market_price REAL)"
        )
        # B141 has per-lot DB baselines ($40 qty 2, $70 qty 1 -> weighted $50.00)
        conn.execute(
            "INSERT INTO card_metadata VALUES (?, ?, ?, ?, ?)",
            ("Welcome to Night City - Beta", "B141", "Foil", "2026-09-14", 40.0),
        )
        conn.execute(
            "INSERT INTO card_metadata VALUES (?, ?, ?, ?, ?)",
            ("Welcome to Night City - Beta", "B141", "Foil", "2026-09-20", 70.0),
        )
        # B142 has a stale SQLite baseline on 2026-09-14 ($55.00), while CSV moved to 2026-10-01 ($95.00)
        conn.execute(
            "INSERT INTO card_metadata VALUES (?, ?, ?, ?, ?)",
            ("Welcome to Night City - Beta", "B142", "Foil", "2026-09-14", 55.0),
        )
        conn.commit()
        conn.close()

        acqs = load_collection_acquisitions(
            collection_path=str(col_csv),
            database_path=str(db_path),
            earliest_beta_date="2026-09-13",
            earliest_retail_date=None,
        )
        all_dates = ["2026-09-14", "2026-09-20", "2026-10-01", "2026-11-09"]
        price_by_date = {
            d: json.loads((p_dir / f"{d}.json").read_text(encoding="utf-8"))["prices"]
            for d in all_dates
        }
        trajs = build_edition_trajectories(
            pairs=[p1, p2],
            edition="beta",
            dates=all_dates,
            price_by_date=price_by_date,
            acquisitions=acqs,
        )
        self.assertAlmostEqual(trajs[0]["acqCost"], 50.0)
        self.assertEqual(trajs[1]["acqDate"], "2026-10-01")
        self.assertAlmostEqual(trajs[1]["acqCost"], 95.0)

    def test_comparison_view_matches_pair_prices_outside_trajectory_window_and_na_sort_attrs(self):
        # Retail #141 only has a pre-cutoff price ($60 on 2026-10-15); #142 is unpriced.
        p1 = IconicCardPair("141", "Priced Card", "Legends", "Foil", 101, 102, 100.0, 60.0, 40.0, 66.7, 0, 0, color="Red", card_type="Legend")
        p2 = IconicCardPair("142", "Unpriced Card", "Legends", "Foil", 103, 104, None, None, None, None, 0, 0, color="Blue", card_type="Legend")

        p_dir = self.test_dir / "cutoff_prices"
        p_dir.mkdir(parents=True, exist_ok=True)
        (p_dir / "2026-10-15.json").write_text(json.dumps({
            "date": "2026-10-15",
            "prices": {"101": {"Foil": {"marketPrice": 100.0}}, "102": {"Foil": {"marketPrice": 60.0}}},
        }), encoding="utf-8")
        (p_dir / "2026-11-09.json").write_text(json.dumps({
            "date": "2026-11-09",
            "prices": {"101": {"Foil": {"marketPrice": 100.0}}},
        }), encoding="utf-8")

        out_html = self.test_dir / "cutoff_comp.html"
        generate_html_dashboard(
            pairs=[p1, p2],
            prices_dir=p_dir,
            collection_path=None,
            output_path=str(out_html),
            database_path=None,
            earliest_beta_date="2026-09-13",
            earliest_retail_date="2026-10-30",
        )
        content = out_html.read_text(encoding="utf-8")
        # Comparison view still reflects paired console summary totals ($100.00 / $60.00)
        self.assertIn("WNC Set Value (1/2 pairs)", content)
        self.assertIn('id="set-val-beta">$100.00</span>', content)
        self.assertIn('id="set-val-retail">$60.00</span>', content)
        # Unpriced trajectory tile uses empty nullable numeric data attributes for bottom sorting
        self.assertIn('data-price="" data-c7d="" data-call="" data-has-acq="0" data-cacq=""', content)
        self.assertIn("compareNullableNums(a.dataset.price, b.dataset.price, -1)", content)
        self.assertIn("compareNullableNums(a.dataset.c7d, b.dataset.c7d, -1)", content)

    def test_run_iconics_comparison_reads_collection_csv_once(self):
        out_html = self.test_dir / "single_read.html"
        read_counter = {"count": 0}
        real_loader = load_validated_collection_rows

        def counting_loader(path):
            read_counter["count"] += 1
            return real_loader(path)

        with patch("tracker.iconics.REPO_ROOT", self.test_dir), \
             patch("tracker.iconics.load_validated_collection_rows", side_effect=counting_loader), \
             patch("tracker.iconics_html.load_validated_collection_rows", side_effect=counting_loader):
            rc, summary = run_iconics_comparison(
                output_path=str(out_html),
                collection_path=str(self.collection_csv),
                cards_path=self.cards_file,
                prices_dir=self.prices_dir,
                database_path=None,
            )

        self.assertEqual(rc, 0)
        self.assertEqual(read_counter["count"], 1)
        self.assertTrue(out_html.is_file())
        self.assertIn("Welcome to Night City", summary)


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
                run_tracker.main(["track-sets"])

        self.assertEqual(ctx.exception.code, 0)
        mock_run.assert_called_once_with(
            output_path=str(self.test_dir / "set_price_trajectories.html"),
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
                run_tracker.main(["track-sets"])

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
                run_tracker.main(["track-sets", "--sync-collection"])

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
                run_tracker.main(["track-sets", "--sync-collection", "--collection", "custom.csv"])
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
                run_tracker.main(["track-sets"])
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
                run_tracker.main(["track-sets"])
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
                run_tracker.main(["track-sets"])
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
                run_tracker.main(["track-sets"])
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
                run_tracker.main(["track-sets", "--sync-collection"])
        self.assertEqual(ctx.exception.code, 0)
        mock_sync.assert_called_once()
        mock_run.assert_called_once_with(
            output_path=str(self.test_dir / "set_price_trajectories.html"),
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
                run_tracker.main(["track-sets", "--sync-collection"])
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
                run_tracker.main(["track-sets", "--sync-collection"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("CardNexus collection validation failed", stderr_buf.getvalue())

    def test_load_validated_collection_rows_validates_columns_and_quantities(self):
        missing_col_csv = self.test_dir / "missing_col.csv"
        missing_col_csv.write_text(
            "name,expansion,printNumber,finish\n"
            "Card 1,Welcome to Night City - Beta,B141,Foil\n",
            encoding="utf-8",
        )
        with self.assertRaises(CollectionValidationError):
            load_validated_collection_rows(str(missing_col_csv))

        empty_csv = self.test_dir / "empty_headerless.csv"
        empty_csv.write_text("", encoding="utf-8")
        with self.assertRaises(CollectionValidationError):
            load_validated_collection_rows(str(empty_csv))

        non_int_csv = self.test_dir / "non_int_qty.csv"
        non_int_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned\n"
            "Card 1,Welcome to Night City - Beta,B141,Foil,oops\n",
            encoding="utf-8",
        )
        with self.assertRaises(CollectionValidationError):
            load_validated_collection_rows(str(non_int_csv))

        neg_csv = self.test_dir / "negative_qty.csv"
        neg_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned\n"
            "Card 1,Welcome to Night City - Beta,B141,Foil,-2\n",
            encoding="utf-8",
        )
        with self.assertRaises(CollectionValidationError):
            load_validated_collection_rows(str(neg_csv))

        zero_csv = self.test_dir / "zero_qty.csv"
        zero_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned\n"
            "Card 1,Welcome to Night City - Beta,B141,Foil,0\n",
            encoding="utf-8",
        )
        rows = load_validated_collection_rows(str(zero_csv))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].qty, 0)

    @patch("run_tracker.load_config")
    @patch("run_tracker.resolve_and_sync_collection")
    @patch("run_tracker.run_iconics_comparison")
    def test_cli_forced_sync_fallback_exits_1_while_default_fallback_succeeds(self, mock_run, mock_resolve, mock_cfg):
        mock_cfg.return_value = self.fake_cfg
        mock_resolve.return_value = run_tracker.CollectionSyncResult(
            collection_path=str(self.collection_file),
            collection_source="CardNexus API (fallback)",
            collection_updated=False,
        )
        mock_run.return_value = (0, "OK")

        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            with self.assertRaises(SystemExit) as ctx:
                run_tracker.main(["track-sets", "--sync-collection"])
        self.assertEqual(ctx.exception.code, 1)
        self.assertIn("CardNexus collection synchronization failed", stderr_buf.getvalue())
        mock_run.assert_not_called()

        with patch("sys.stdout", io.StringIO()):
            with self.assertRaises(SystemExit) as ctx_default:
                run_tracker.main(["track-sets"])
        self.assertEqual(ctx_default.exception.code, 0)
        mock_run.assert_called_once()


class TestSetTrackerExpansionAndState(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.prices_dir = self.test_dir / "prices"
        self.prices_dir.mkdir(parents=True, exist_ok=True)
        self.cards_file = self.test_dir / "cards.json"
        self.collection_csv = self.test_dir / "collection.csv"

    def tearDown(self):
        self.temp_dir.cleanup()

    def _base_wnc_iconic_catalog_entries(self):
        return {
            "1": {"name": "Welcome to Night City - Beta"},
            "2": {"name": "Welcome to Night City - Retail"},
            "3": {"name": "Box Toppers - Beta"},
            "4": {"name": "Box Toppers - Retail"},
            "5": {"name": "Night City Brawl Season 1"},
        }

    def test_box_topper_pairing_normalization_and_expansion_metadata(self):
        catalog = {
            "_groups": self._base_wnc_iconic_catalog_entries(),
            "101": {
                "productId": 101,
                "name": "Adam Smasher",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B141",
                "rarity": "IL",
                "color": "Red",
                "cardType": "Legend",
            },
            "102": {
                "productId": 102,
                "name": "Adam Smasher",
                "groupName": "Welcome to Night City - Retail",
                "printNumber": "141",
                "rarity": "IL",
                "color": "Red",
                "cardType": "Legend",
            },
            "201": {
                "productId": 201,
                "name": "David Martinez (Topper)",
                "groupName": "Box Toppers - Beta",
                "printNumber": "B001",
                "rarity": "NR",
                "color": "Yellow",
                "cardType": "Legend",
            },
            "202": {
                "productId": 202,
                "name": "David Martinez (Topper)",
                "groupName": "Box Toppers - Retail",
                "printNumber": "001",
                "rarity": "Nova Rare",
                "color": "Yellow",
                "cardType": "Legend",
            },
            "203": {
                "productId": 203,
                "name": "Lucy Kushinada (Topper)",
                "groupName": "Box Toppers - Beta",
                "printNumber": "B002",
                "rarity": "NR",
                "color": "Blue",
                "cardType": "Unit",
            },
            "204": {
                "productId": 204,
                "name": "Lucy Kushinada (Topper)",
                "groupName": "Box Toppers - Retail",
                "printNumber": "002",
                "rarity": "Nova Rare",
                "color": "Blue",
                "cardType": "Unit",
            },
            "205": {
                "productId": 205,
                "name": "Rebecca (Topper)",
                "groupName": "Box Toppers - Beta",
                "printNumber": "B003",
                "rarity": "Nova Rare",
                "color": "Red",
                "cardType": "Unit",
            },
            "206": {
                "productId": 206,
                "name": "Rebecca (Topper)",
                "groupName": "Box Toppers - Retail",
                "printNumber": "003",
                "rarity": "Nova Rare",
                "color": "Red",
                "cardType": "Unit",
            },
        }
        self.cards_file.write_text(json.dumps(catalog), encoding="utf-8")

        prices = {
            "101": {"Foil": {"marketPrice": 50.0}},
            "102": {"Foil": {"marketPrice": 25.0}},
            "201": {"Foil": {"marketPrice": 80.0}},
            "202": {"Foil": {"marketPrice": 35.0}},
            "203": {"Foil": {"marketPrice": 60.0}},
            "204": {"Foil": {"marketPrice": 22.0}},
            "205": {"Foil": {"marketPrice": 45.0}},
            "206": {"Foil": {"marketPrice": 18.0}},
        }
        (self.prices_dir / "2026-11-09.json").write_text(
            json.dumps({"date": "2026-11-09", "prices": prices}), encoding="utf-8"
        )
        self.collection_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned\n"
            "David Martinez (Topper),Box Toppers - Beta,B001,Foil,2\n"
            "Lucy Kushinada (Topper),Box Toppers - Retail,002,Foil,1\n",
            encoding="utf-8",
        )

        pairs, _, _ = load_iconic_pairs(
            cards_path=self.cards_file,
            prices_dir=self.prices_dir,
            collection_path=str(self.collection_csv),
        )

        bt_pairs = [p for p in pairs if p.tier == TIER_MAP["BT"]]
        self.assertEqual(len(bt_pairs), 3)
        self.assertEqual([p.print_number for p in bt_pairs], ["001", "002", "003"])
        for p in bt_pairs:
            self.assertEqual(p.beta_expansion, "Box Toppers - Beta")
            self.assertEqual(p.retail_expansion, "Box Toppers - Retail")
            self.assertEqual(p.valid_finish, "Foil")

        self.assertEqual(bt_pairs[0].beta_owned, 2)
        self.assertEqual(bt_pairs[0].retail_owned, 0)
        self.assertEqual(bt_pairs[1].beta_owned, 0)
        self.assertEqual(bt_pairs[1].retail_owned, 1)

    def test_unpaired_box_topper_price_excluded_from_comparative_totals(self):
        p_paired = IconicCardPair(
            "001", "Topper One", "Box Toppers", "Foil",
            201, 202, 40.0, 15.0, 25.0, 166.7, 1, 0,
            color="Yellow", card_type="Legend",
            beta_expansion="Box Toppers - Beta",
            retail_expansion="Box Toppers - Retail",
        )
        p_unpaired = IconicCardPair(
            "002", "Topper Unpaired", "Box Toppers", "Foil",
            203, 204, 100.0, None, None, None, 0, 0,
            color="Blue", card_type="Unit",
            beta_expansion="Box Toppers - Beta",
            retail_expansion="Box Toppers - Retail",
        )
        summary = build_tier_summary("Box Toppers", [p_paired, p_unpaired])
        self.assertEqual(summary.total_cards, 2)
        self.assertEqual(summary.priced_pairs_count, 1)
        self.assertEqual(summary.beta_set_total, 40.0)
        self.assertEqual(summary.retail_set_total, 15.0)
        self.assertEqual(summary.beta_median, 40.0)
        self.assertEqual(summary.spread, 25.0)

        (self.prices_dir / "2026-11-09.json").write_text(
            json.dumps({
                "date": "2026-11-09",
                "prices": {
                    "201": {"Foil": {"marketPrice": 40.0}},
                    "202": {"Foil": {"marketPrice": 15.0}},
                    "203": {"Foil": {"marketPrice": 100.0}},
                    "204": {},
                },
            }),
            encoding="utf-8",
        )
        out_html = self.test_dir / "bt_unpaired.html"
        generate_html_dashboard(
            pairs=[p_paired, p_unpaired],
            prices_dir=self.prices_dir,
            collection_path=None,
            output_path=str(out_html),
            earliest_beta_date=None,
            earliest_retail_date=None,
        )
        html_text = out_html.read_text(encoding="utf-8")
        self.assertIn("WNC Set Value (1/2 pairs)", html_text)
        self.assertIn('id="pill-tier-BT"', html_text)
        self.assertIn('id="section-BT"', html_text)
        self.assertIn("Total (1/2 pairs)", html_text)

    def test_unrecognized_box_topper_rarity_raises_value_error(self):
        catalog = {
            "_groups": self._base_wnc_iconic_catalog_entries(),
            "101": {
                "productId": 101,
                "name": "Adam Smasher",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B141",
                "rarity": "IL",
            },
            "102": {
                "productId": 102,
                "name": "Adam Smasher",
                "groupName": "Welcome to Night City - Retail",
                "printNumber": "141",
                "rarity": "IL",
            },
            "201": {
                "productId": 201,
                "name": "Maine Topper",
                "groupName": "Box Toppers - Beta",
                "printNumber": "B001",
                "rarity": "NR",
            },
            "202": {
                "productId": 202,
                "name": "Maine Topper",
                "groupName": "Box Toppers - Retail",
                "printNumber": "001",
                "rarity": "R",
            },
        }
        self.cards_file.write_text(json.dumps(catalog), encoding="utf-8")
        (self.prices_dir / "2026-11-09.json").write_text(
            json.dumps({
                "date": "2026-11-09",
                "prices": {
                    "101": {"Foil": {"marketPrice": 10.0}},
                    "102": {"Foil": {"marketPrice": 5.0}},
                    "201": {"Foil": {"marketPrice": 20.0}},
                    "202": {"Foil": {"marketPrice": 10.0}},
                },
            }),
            encoding="utf-8",
        )

        with self.assertRaises(ValueError) as ctx:
            load_iconic_pairs(cards_path=self.cards_file, prices_dir=self.prices_dir)

        msg = str(ctx.exception)
        self.assertIn("Box Toppers - Retail", msg)
        self.assertIn("Maine Topper", msg)
        self.assertIn("001", msg)
        self.assertIn("R", msg)

        ncb_bad_catalog = {
            "_groups": self._base_wnc_iconic_catalog_entries(),
            "301": {
                "productId": 301,
                "name": "Bad Promo",
                "groupName": "Night City Brawl Season 1",
                "printNumber": "001",
                "rarity": "SR",
            },
        }
        bad_cards = self.test_dir / "bad_ncb_cards.json"
        bad_cards.write_text(json.dumps(ncb_bad_catalog), encoding="utf-8")
        with self.assertRaises(ValueError) as ncb_ctx:
            load_ncb_cards(cards_path=bad_cards, prices_dir=self.prices_dir)
        self.assertIn("Night City Brawl Season 1", str(ncb_ctx.exception))
        self.assertIn("SR", str(ncb_ctx.exception))

    def test_minimal_catalog_without_ncb_or_bt_and_late_earliest_ncb_date(self):
        catalog = {
            "_groups": {"1": {"name": "Welcome to Night City - Beta"}, "2": {"name": "Welcome to Night City - Retail"}},
            "101": {
                "productId": 101,
                "name": "Adam Smasher",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B141",
                "rarity": "IL",
                "color": "Red",
                "cardType": "Legend",
            },
            "102": {
                "productId": 102,
                "name": "Adam Smasher",
                "groupName": "Welcome to Night City - Retail",
                "printNumber": "141",
                "rarity": "IL",
                "color": "Red",
                "cardType": "Legend",
            },
        }
        self.cards_file.write_text(json.dumps(catalog), encoding="utf-8")
        (self.prices_dir / "2026-11-09.json").write_text(
            json.dumps({
                "date": "2026-11-09",
                "prices": {"101": {"Foil": {"marketPrice": 25.0}}, "102": {"Foil": {"marketPrice": 10.0}}},
            }),
            encoding="utf-8",
        )
        self.assertEqual(load_ncb_cards(cards_path=self.cards_file, prices_dir=self.prices_dir), [])

        self.collection_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned\n"
            "Adam Smasher,Welcome to Night City - Beta,B141,Foil,1\n",
            encoding="utf-8",
        )
        out_html = self.test_dir / "minimal_wnc.html"
        with patch("tracker.iconics.REPO_ROOT", self.test_dir), \
             patch("tracker.iconics.load_config", side_effect=AssertionError("unexpected load_config")):
            rc, summary = run_iconics_comparison(
                output_path=str(out_html),
                collection_path=str(self.collection_csv),
                cards_path=self.cards_file,
                prices_dir=self.prices_dir,
                database_path=None,
            )
        self.assertEqual(rc, 0)
        self.assertNotIn("Night City Brawl S1", summary)
        html_text = out_html.read_text(encoding="utf-8")
        self.assertNotIn('id="section-BT"', html_text)
        self.assertNotIn('id="pill-tier-BT"', html_text)
        self.assertNotIn('id="set-toggle"', html_text)
        self.assertNotIn('id="section-NR"', html_text)
        self.assertIn("var hasNcb = false;", html_text)

        late_html = self.test_dir / "late_ncb.html"
        promo = PromoSetCard("001", "Late Promo", "Nova Rare", "Foil", 901, 50.0, 1, color="Red", card_type="Unit")
        generate_html_dashboard(
            pairs=[IconicCardPair("141", "Adam Smasher", "Legends", "Foil", 101, 102, 25.0, 10.0, 15.0, 150.0, 0, 0)],
            prices_dir=self.prices_dir,
            collection_path=None,
            output_path=str(late_html),
            earliest_beta_date=None,
            earliest_retail_date=None,
            earliest_ncb_date="2099-01-01",
            ncb_cards=[promo],
        )
        late_text = late_html.read_text(encoding="utf-8")
        self.assertIn('id="set-toggle"', late_text)
        self.assertIn('id="section-NR"', late_text)
        self.assertIn("var hasNcb = true;", late_text)
        self.assertIn("activeSetState = applySetTierStatsToggle(", late_text)
        nr_section = late_text.split('id="section-NR"', 1)[1].split('id="view-comparison"', 1)[0]
        self.assertIn("Nova Rares (1)", nr_section)
        self.assertIn('<span class="price-main">N/A</span>', nr_section)
        self.assertIn('data-price=""', nr_section)

    def test_load_ncb_cards_and_trajectories_baseline_carry_forward_and_clamping(self):
        catalog = {
            "_groups": self._base_wnc_iconic_catalog_entries(),
            "301": {
                "productId": 301,
                "name": "Chrome Anticipation",
                "groupName": "Night City Brawl Season 1",
                "printNumber": "001",
                "rarity": "NR",
                "color": "Red",
                "cardType": "Program",
            },
            "302": {
                "productId": 302,
                "name": "Kiroshi Optics",
                "groupName": "Night City Brawl Season 1",
                "printNumber": "002",
                "rarity": "NR",
                "color": "Blue",
                "cardType": "Gear",
            },
            "310": {
                "productId": 310,
                "name": "Cyberware Calibration",
                "groupName": "Night City Brawl Season 1",
                "printNumber": "010",
                "rarity": "Nova Rare",
                "color": "Green",
                "cardType": "Program",
            },
        }
        self.cards_file.write_text(json.dumps(catalog), encoding="utf-8")

        snapshots = {
            "2026-11-05": {"301": {"Foil": {"marketPrice": 99.0}}, "302": {}, "310": {"Foil": {"marketPrice": 5.0}}},
            "2026-11-06": {"301": {"Foil": {"marketPrice": 10.0}}, "302": {"Foil": {"marketPrice": 0.0}}, "310": {"Foil": {"marketPrice": 8.0}}},
            "2026-11-07": {"301": {}, "302": {"Foil": {"marketPrice": 0.0}}, "310": {"Foil": {"marketPrice": 9.0}}},
            "2026-11-08": {"301": {"Foil": {"marketPrice": 14.0}}, "302": {"Foil": {"marketPrice": 30.0}}, "310": {"Foil": {"marketPrice": 10.0}}},
            "2026-11-09": {"302": {"Foil": {"marketPrice": 36.0}}, "310": {"Foil": {"marketPrice": 12.0}}},
        }
        for d, p in snapshots.items():
            (self.prices_dir / f"{d}.json").write_text(json.dumps({"date": d, "prices": p}), encoding="utf-8")

        self.collection_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\n"
            "Chrome Anticipation,Night City Brawl Season 1,001,Foil,2,2026-10-01\n",
            encoding="utf-8",
        )

        ncb_cards = load_ncb_cards(
            cards_path=self.cards_file,
            prices_dir=self.prices_dir,
            collection_path=str(self.collection_csv),
        )
        self.assertEqual([c.print_number for c in ncb_cards], ["001", "002", "010"])
        self.assertEqual([c.rarity for c in ncb_cards], ["Nova Rare", "Nova Rare", "Nova Rare"])
        self.assertEqual(ncb_cards[0].price, 14.0)
        self.assertEqual(ncb_cards[0].owned, 2)
        self.assertEqual(ncb_cards[1].price, 36.0)

        ncb_dates = ["2026-11-06", "2026-11-07", "2026-11-08", "2026-11-09"]
        price_by_date = {d: snapshots[d] for d in ncb_dates}
        acqs = load_collection_acquisitions(
            collection_path=str(self.collection_csv),
            database_path=None,
            earliest_beta_date="2026-09-13",
            earliest_retail_date="2026-10-30",
            earliest_ncb_date="2026-11-06",
        )
        self.assertEqual(acqs[("Night City Brawl Season 1", "001", "Foil")]["date"], "2026-11-06")

        trajs = build_ncb_trajectories(
            ncb_cards=ncb_cards,
            dates=ncb_dates,
            price_by_date=price_by_date,
            acquisitions=acqs,
        )
        t001, t002 = trajs[0], trajs[1]
        self.assertEqual(t001["points"], [("2026-11-06", 10.0), ("2026-11-07", 10.0), ("2026-11-08", 14.0), ("2026-11-09", 14.0)])
        self.assertEqual(t001["acqDate"], "2026-11-06")
        self.assertEqual(t001["acqCost"], 10.0)
        self.assertEqual(t001["acqDeltaDollar"], 4.0)

        self.assertEqual(t002["baselineDate"], "2026-11-08")
        self.assertEqual(t002["baselinePrice"], 30.0)
        self.assertEqual(t002["changeAll"], 6.0)
        self.assertAlmostEqual(t002["changeAllPct"], 20.0)

    def _require_node(self) -> str:
        node_bin = shutil.which("node")
        if not node_bin:
            if os.environ.get("REQUIRE_NODE"):
                self.fail("node is required when REQUIRE_NODE is set")
            self.skipTest("node not installed")
        return node_bin

    def test_pure_js_set_state_migration_isolation_and_full_descriptor(self):
        node_bin = self._require_node()
        driver = f"""
const vm = require('vm');
const context = {{}};
vm.createContext(context);
vm.runInContext({json.dumps(_SET_STATE_JS)}, context);

const meta = {{
  totalCardCount: 37,
  ncbCardCount: 18,
  wncBetaRangeLabel: 'B001 \\u2192 B171',
  wncRetailRangeLabel: '001 \\u2192 171',
  ncbRangeLabel: '001 \\u2192 018'
}};

const defFilters = context.defaultSetFilters();
const fresh = context.normalizeSavedFilterState(null);
const legacyFlat = context.normalizeSavedFilterState({{
  edition: 'comparison',
  anchor: 'l7d',
  q: 'Panam',
  o: ['owned'],
  r: ['IL'],
  c: ['Red'],
  t: ['Legend'],
  s: 'price-desc',
  b: 'IL',
  statsOpen: true
}});
const legacyLong = context.normalizeSavedFilterState({{
  edition: 'retail',
  search: 'Judy',
  ownership: ['unowned'],
  rarity: ['IS'],
  sort: 'price-asc'
}});
const afterToNcb = context.switchSetFilterState(
  {{ set: 'wnc', edition: 'retail', anchor: 'acq', filtersBySet: {{ wnc: context.defaultSetFilters(), ncb: context.defaultSetFilters() }} }},
  'ncb',
  {{ q: 'Smasher', o: ['owned'], r: ['IL'], c: ['Red'], t: ['Legend'], s: 'price-desc', b: 'IL', statsOpen: {{ IL: true, BT: false }} }}
);
const backToWnc = context.switchSetFilterState(
  afterToNcb,
  'wnc',
  {{ q: 'Chrome', o: [], r: [], c: ['Blue'], t: ['Program'], s: 'gain-desc', b: 'all', statsOpen: {{ NR: false }} }}
);
const clampedSavedNcb = context.normalizeSavedFilterState(
  {{ set: 'ncb', edition: 'retail', anchor: 'acq', filtersBySet: {{ wnc: context.defaultSetFilters(), ncb: context.defaultSetFilters() }} }},
  false
);
const clampedSwitchNcb = context.switchSetFilterState(
  {{ set: 'wnc', edition: 'beta', anchor: 'acq', filtersBySet: {{ wnc: context.defaultSetFilters(), ncb: context.defaultSetFilters() }} }},
  'ncb',
  {{ q: 'Smasher', o: [], r: [], c: [], t: [], s: 'num', b: 'all', statsOpen: {{ IL: false }} }},
  false
);
const emptyRangeWnc = context.computeSetViewState('wnc', 'beta', {{
  totalCardCount: 0,
  ncbCardCount: 0,
  wncBetaRangeLabel: '',
  wncRetailRangeLabel: '',
  ncbRangeLabel: ''
}});
const resolvedStats = {{
  ilExplicitFalseOnWide: context.resolveTierStatsOpen({{ IL: false, BT: true }}, 'IL', true),
  btExplicitTrueOnNarrow: context.resolveTierStatsOpen({{ IL: false, BT: true }}, 'BT', false),
  ioUnsetOnWide: context.resolveTierStatsOpen({{ IL: false, BT: true }}, 'IO', true),
  isUnsetOnNarrow: context.resolveTierStatsOpen({{ IL: false, BT: true }}, 'IS', false),
  nrUnsetOnWide: context.resolveTierStatsOpen({{}}, 'NR', true),
  nrExplicitFalseOnWide: context.resolveTierStatsOpen({{ NR: false }}, 'NR', true),
}};
const toggledWncStep1 = context.applySetTierStatsToggle(
  {{ set: 'wnc', edition: 'beta', anchor: 'acq', filtersBySet: {{ wnc: context.defaultSetFilters(), ncb: context.defaultSetFilters() }} }},
  'wnc',
  'IL',
  false,
  true
);
const toggledWncStep2 = context.applySetTierStatsToggle(toggledWncStep1, 'wnc', 'BT', true, true);
const afterFilterSave = context.switchSetFilterState(
  toggledWncStep2,
  'ncb',
  Object.assign({{}}, toggledWncStep2.filtersBySet.wnc, {{ q: 'Smasher' }}),
  true
);
const toggledNcb = context.applySetTierStatsToggle(afterFilterSave, 'ncb', 'NR', false, true);
const roundTripped = context.normalizeSavedFilterState(JSON.parse(JSON.stringify(toggledNcb)), true);
const out = {{
  defFilters,
  fresh,
  legacyFlat,
  legacyLong,
  afterToNcb,
  backToWnc,
  clampedSavedNcb,
  clampedSwitchNcb,
  emptyRangeWnc,
  resolvedStats,
  roundTripped,
  descriptors: {{
    wncBeta: context.computeSetViewState('wnc', 'beta', meta),
    wncRetail: context.computeSetViewState('wnc', 'retail', meta),
    wncComparison: context.computeSetViewState('wnc', 'comparison', meta),
    ncbBeta: context.computeSetViewState('ncb', 'beta', meta),
    ncbRetail: context.computeSetViewState('ncb', 'retail', meta),
    ncbComparison: context.computeSetViewState('ncb', 'comparison', meta),
  }}
}};
process.stdout.write(JSON.stringify(out));
"""
        proc = subprocess.run(
            [node_bin, "-e", driver],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr)
        res = json.loads(proc.stdout)

        clean_default = {"q": "", "o": [], "r": [], "c": [], "t": [], "s": "num", "b": "all", "statsOpen": {}}
        self.assertEqual(res["defFilters"], clean_default)
        self.assertEqual(res["fresh"]["set"], "wnc")
        self.assertEqual(res["fresh"]["edition"], "beta")
        self.assertEqual(res["fresh"]["filtersBySet"]["ncb"], clean_default)

        self.assertEqual(res["legacyFlat"]["set"], "wnc")
        self.assertEqual(res["legacyFlat"]["edition"], "comparison")
        self.assertEqual(res["legacyFlat"]["anchor"], "l7d")
        self.assertEqual(res["legacyFlat"]["filtersBySet"]["wnc"], {
            "q": "Panam", "o": ["owned"], "r": ["IL"], "c": ["Red"], "t": ["Legend"], "s": "price-desc", "b": "IL", "statsOpen": {},
        })
        self.assertEqual(res["legacyFlat"]["filtersBySet"]["ncb"], clean_default)

        self.assertEqual(res["legacyLong"]["set"], "wnc")
        self.assertEqual(res["legacyLong"]["edition"], "retail")
        self.assertEqual(res["legacyLong"]["filtersBySet"]["wnc"]["q"], "Judy")
        self.assertEqual(res["legacyLong"]["filtersBySet"]["wnc"]["o"], ["unowned"])
        self.assertEqual(res["legacyLong"]["filtersBySet"]["wnc"]["r"], ["IS"])
        self.assertEqual(res["legacyLong"]["filtersBySet"]["wnc"]["s"], "price-asc")

        self.assertEqual(res["afterToNcb"]["set"], "ncb")
        self.assertEqual(res["afterToNcb"]["edition"], "retail")
        self.assertEqual(res["afterToNcb"]["filtersBySet"]["wnc"]["q"], "Smasher")
        self.assertEqual(res["afterToNcb"]["filtersBySet"]["wnc"]["statsOpen"], {"IL": True, "BT": False})
        self.assertEqual(res["afterToNcb"]["filtersBySet"]["ncb"], clean_default)

        self.assertEqual(res["backToWnc"]["set"], "wnc")
        self.assertEqual(res["backToWnc"]["edition"], "retail")
        self.assertEqual(res["backToWnc"]["filtersBySet"]["wnc"]["q"], "Smasher")
        self.assertEqual(res["backToWnc"]["filtersBySet"]["wnc"]["statsOpen"], {"IL": True, "BT": False})
        self.assertEqual(res["backToWnc"]["filtersBySet"]["ncb"]["q"], "Chrome")
        self.assertEqual(res["backToWnc"]["filtersBySet"]["ncb"]["c"], ["Blue"])
        self.assertEqual(res["backToWnc"]["filtersBySet"]["ncb"]["statsOpen"], {"NR": False})

        self.assertEqual(res["clampedSavedNcb"]["set"], "wnc")
        self.assertEqual(res["clampedSwitchNcb"]["set"], "wnc")
        self.assertEqual(res["clampedSwitchNcb"]["filtersBySet"]["wnc"]["statsOpen"], {"IL": False})
        self.assertEqual(res["emptyRangeWnc"]["sortNumLabel"], "Print #")
        self.assertEqual(res["resolvedStats"], {
            "ilExplicitFalseOnWide": False,
            "btExplicitTrueOnNarrow": True,
            "ioUnsetOnWide": True,
            "isUnsetOnNarrow": False,
            "nrUnsetOnWide": True,
            "nrExplicitFalseOnWide": False,
        })
        self.assertEqual(res["roundTripped"]["filtersBySet"]["wnc"]["q"], "Smasher")
        self.assertEqual(res["roundTripped"]["filtersBySet"]["wnc"]["statsOpen"], {"IL": False, "BT": True})
        self.assertNotIn("IO", res["roundTripped"]["filtersBySet"]["wnc"]["statsOpen"])
        self.assertNotIn("IS", res["roundTripped"]["filtersBySet"]["wnc"]["statsOpen"])
        self.assertEqual(res["roundTripped"]["filtersBySet"]["ncb"]["statsOpen"], {"NR": False})

        descriptors = res["descriptors"]
        expected_keys = {
            "title",
            "showClusterView",
            "showMsRarity",
            "showLegendTypeItem",
            "showSectionNR",
            "showWncTiers",
            "showComparisonView",
            "showBasisCluster",
            "sortNumLabel",
            "countDenominator",
        }
        for k, desc in descriptors.items():
            self.assertEqual(set(desc.keys()), expected_keys, msg=f"Key mismatch for {k}")

        self.assertEqual(descriptors["wncBeta"], {
            "title": "Welcome to Night City \u2013 Iconic & Promo Price Trajectories",
            "showClusterView": True,
            "showMsRarity": True,
            "showLegendTypeItem": True,
            "showSectionNR": False,
            "showWncTiers": True,
            "showComparisonView": False,
            "showBasisCluster": True,
            "sortNumLabel": "Print # (B001 \u2192 B171)",
            "countDenominator": 37,
        })
        self.assertEqual(descriptors["wncRetail"], {
            "title": "Welcome to Night City \u2013 Iconic & Promo Price Trajectories",
            "showClusterView": True,
            "showMsRarity": True,
            "showLegendTypeItem": True,
            "showSectionNR": False,
            "showWncTiers": True,
            "showComparisonView": False,
            "showBasisCluster": True,
            "sortNumLabel": "Print # (001 \u2192 171)",
            "countDenominator": 37,
        })
        self.assertEqual(descriptors["wncComparison"], {
            "title": "Welcome to Night City \u2013 Iconic & Promo Price Trajectories",
            "showClusterView": True,
            "showMsRarity": True,
            "showLegendTypeItem": True,
            "showSectionNR": False,
            "showWncTiers": False,
            "showComparisonView": True,
            "showBasisCluster": False,
            "sortNumLabel": "Print # (001 \u2192 171)",
            "countDenominator": 37,
        })
        expected_ncb = {
            "title": "Night City Brawl Season 1 \u2013 Nova Rare Price Trajectories",
            "showClusterView": False,
            "showMsRarity": False,
            "showLegendTypeItem": False,
            "showSectionNR": True,
            "showWncTiers": False,
            "showComparisonView": False,
            "showBasisCluster": True,
            "sortNumLabel": "Print # (001 \u2192 018)",
            "countDenominator": 18,
        }
        self.assertEqual(descriptors["ncbBeta"], expected_ncb)
        self.assertEqual(descriptors["ncbRetail"], expected_ncb)
        self.assertEqual(descriptors["ncbComparison"], expected_ncb)

    def test_console_summary_76_col_width_with_box_toppers_and_ncb(self):
        summaries = [
            TierSummary("Legends", 10, 10, 4, 8, 125.00, 45.00, 80.00),
            TierSummary("Others", 14, 14, 2, 10, 75.50, 22.00, 53.50),
            TierSummary("Secrets", 7, 7, 1, 5, 199.99, 65.00, 134.99),
            TierSummary("Box Toppers", 6, 6, 6, 0, 140.00, 40.00, 100.00),
        ]
        pairs = [
            IconicCardPair(f"{i:03d}", f"Card {i}", "Legends", "Foil", i, i + 100, 50.0, 20.0, 30.0, 150.0, 1, 1)
            for i in range(1, 38)
        ]
        ncb_cards = [
            PromoSetCard(f"{i:03d}", f"NCB {i}", "Nova Rare", "Foil", 500 + i, 25.0 if i <= 16 else None, 1 if i <= 8 else 0)
            for i in range(1, 19)
        ]
        summary = format_console_summary(
            tier_summaries=summaries,
            pairs=pairs,
            output_path="set_price_trajectories.html",
            collection_name="active_collection.csv",
            collection_mtime_date="2026-11-14",
            ncb_cards=ncb_cards,
        )
        lines = summary.splitlines()
        self.assertLessEqual(max(len(line) for line in lines), 76)
        self.assertIn("=============== Welcome to Night City: Iconics & Box Toppers ===============", summary)
        self.assertIn("Owned WNC Value:", summary)
        self.assertIn("Box Toppers (6)     6/6          0/6", summary)
        self.assertIn("Night City Brawl S1: Nova Rares", summary)
        self.assertIn("8/18 (8 cards)", summary)
        self.assertIn("Set (16/18 priced):", summary)

    def test_overlapping_print_numbers_across_rarities_do_not_collide_in_comparison(self):
        il_pair = IconicCardPair(
            "001", "Iconic Overlap", "Legends", "Foil",
            101, 102, 120.0, 55.0, 65.0, 118.2, 3, 2,
            color="Red", card_type="Legend",
            beta_expansion="Welcome to Night City - Beta",
            retail_expansion="Welcome to Night City - Retail",
        )
        bt_pair = IconicCardPair(
            "001", "Box Topper Overlap", "Box Toppers", "Foil",
            201, 202, 85.0, 30.0, 55.0, 183.3, 1, 4,
            color="Blue", card_type="Unit",
            beta_expansion="Box Toppers - Beta",
            retail_expansion="Box Toppers - Retail",
        )
        (self.prices_dir / "2026-11-09.json").write_text(
            json.dumps({
                "date": "2026-11-09",
                "prices": {
                    "101": {"Foil": {"marketPrice": 120.0}},
                    "102": {"Foil": {"marketPrice": 55.0}},
                    "201": {"Foil": {"marketPrice": 85.0}},
                    "202": {"Foil": {"marketPrice": 30.0}},
                },
            }),
            encoding="utf-8",
        )
        out_html = self.test_dir / "overlap_comp.html"
        generate_html_dashboard(
            pairs=[il_pair, bt_pair],
            prices_dir=self.prices_dir,
            collection_path=None,
            output_path=str(out_html),
            earliest_beta_date=None,
            earliest_retail_date=None,
        )
        html_text = out_html.read_text(encoding="utf-8")
        table_il = html_text.split('id="table-IL"', 1)[1].split("</table>", 1)[0]
        table_bt = html_text.split('id="table-BT"', 1)[1].split("</table>", 1)[0]
        self.assertIn("Iconic Overlap", table_il)
        self.assertIn("$120.00", table_il)
        self.assertIn("$55.00", table_il)
        self.assertIn('id="total-b-owned-IL">3</td>', table_il)
        self.assertIn('id="total-r-owned-IL">2</td>', table_il)
        self.assertNotIn("Box Topper Overlap", table_il)

        self.assertIn("Box Topper Overlap", table_bt)
        self.assertIn("$85.00", table_bt)
        self.assertIn("$30.00", table_bt)
        self.assertIn('id="total-b-owned-BT">1</td>', table_bt)
        self.assertIn('id="total-r-owned-BT">4</td>', table_bt)
        self.assertNotIn("Iconic Overlap", table_bt)

    def test_run_iconics_comparison_loads_catalog_context_once(self):
        catalog = {
            "_groups": self._base_wnc_iconic_catalog_entries(),
            "101": {
                "productId": 101,
                "name": "Adam Smasher",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B141",
                "rarity": "IL",
                "color": "Red",
                "cardType": "Legend",
            },
            "102": {
                "productId": 102,
                "name": "Adam Smasher",
                "groupName": "Welcome to Night City - Retail",
                "printNumber": "141",
                "rarity": "IL",
                "color": "Red",
                "cardType": "Legend",
            },
            "301": {
                "productId": 301,
                "name": "Chrome Anticipation",
                "groupName": "Night City Brawl Season 1",
                "printNumber": "001",
                "rarity": "Nova Rare",
                "color": "Red",
                "cardType": "Program",
            },
        }
        self.cards_file.write_text(json.dumps(catalog), encoding="utf-8")
        (self.prices_dir / "2026-11-09.json").write_text(
            json.dumps({
                "date": "2026-11-09",
                "prices": {
                    "101": {"Foil": {"marketPrice": 25.0}},
                    "102": {"Foil": {"marketPrice": 10.0}},
                    "301": {"Foil": {"marketPrice": 15.0}},
                },
            }),
            encoding="utf-8",
        )
        self.collection_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned\n"
            "Adam Smasher,Welcome to Night City - Beta,B141,Foil,1\n",
            encoding="utf-8",
        )
        out_html = self.test_dir / "single_catalog_ctx.html"
        with patch("tracker.iconics.REPO_ROOT", self.test_dir), \
             patch("tracker.iconics.load_config", side_effect=AssertionError("unexpected load_config")), \
             patch("tracker.iconics._load_catalog_and_price_context", wraps=_load_catalog_and_price_context) as mock_ctx:
            rc, _ = run_iconics_comparison(
                output_path=str(out_html),
                collection_path=str(self.collection_csv),
                cards_path=self.cards_file,
                prices_dir=self.prices_dir,
                database_path=None,
            )
        self.assertEqual(rc, 0)
        self.assertEqual(mock_ctx.call_count, 1)

    def test_catalog_context_validates_bound_paths(self):
        catalog = {
            "_groups": self._base_wnc_iconic_catalog_entries(),
            "101": {
                "productId": 101,
                "name": "Adam Smasher",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B141",
                "rarity": "IL",
            },
            "102": {
                "productId": 102,
                "name": "Adam Smasher",
                "groupName": "Welcome to Night City - Retail",
                "printNumber": "141",
                "rarity": "IL",
            },
            "301": {
                "productId": 301,
                "name": "Chrome Anticipation",
                "groupName": "Night City Brawl Season 1",
                "printNumber": "001",
                "rarity": "Nova Rare",
            },
        }
        self.cards_file.write_text(json.dumps(catalog), encoding="utf-8")
        (self.prices_dir / "2026-11-09.json").write_text(
            json.dumps({
                "date": "2026-11-09",
                "prices": {
                    "101": {"Foil": {"marketPrice": 25.0}},
                    "102": {"Foil": {"marketPrice": 10.0}},
                    "301": {"Foil": {"marketPrice": 15.0}},
                },
            }),
            encoding="utf-8",
        )
        ctx = _load_catalog_and_price_context(self.cards_file, self.prices_dir)
        pairs, _, _ = load_iconic_pairs(cards_path=self.cards_file, prices_dir=self.prices_dir, catalog_context=ctx)
        ncb_cards = load_ncb_cards(cards_path=self.cards_file, prices_dir=self.prices_dir, catalog_context=ctx)
        self.assertEqual(len(pairs), 1)
        self.assertEqual(len(ncb_cards), 1)

        other_cards = self.test_dir / "other_cards.json"
        other_prices = self.test_dir / "other_prices"
        with self.assertRaises(ValueError):
            load_iconic_pairs(cards_path=other_cards, prices_dir=self.prices_dir, catalog_context=ctx)
        with self.assertRaises(ValueError):
            load_ncb_cards(cards_path=self.cards_file, prices_dir=other_prices, catalog_context=ctx)

    def test_empty_wnc_collection_match_emits_four_group_warning(self):
        catalog = {
            "_groups": self._base_wnc_iconic_catalog_entries(),
            "101": {
                "productId": 101,
                "name": "Adam Smasher",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B141",
                "rarity": "IL",
            },
            "102": {
                "productId": 102,
                "name": "Adam Smasher",
                "groupName": "Welcome to Night City - Retail",
                "printNumber": "141",
                "rarity": "IL",
            },
        }
        self.cards_file.write_text(json.dumps(catalog), encoding="utf-8")
        (self.prices_dir / "2026-11-09.json").write_text(
            json.dumps({"date": "2026-11-09", "prices": {"101": {"Foil": {"marketPrice": 25.0}}, "102": {"Foil": {"marketPrice": 10.0}}}}),
            encoding="utf-8",
        )
        self.collection_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned\n"
            "Chrome Anticipation,Night City Brawl Season 1,001,Foil,1\n",
            encoding="utf-8",
        )
        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            load_iconic_pairs(
                cards_path=self.cards_file,
                prices_dir=self.prices_dir,
                collection_path=str(self.collection_csv),
            )
        self.assertIn(
            "Warning: Collection contains records but zero rows matched "
            "'Welcome to Night City - Beta', 'Welcome to Night City - Retail', "
            "'Box Toppers - Beta', or 'Box Toppers - Retail'.",
            stderr_buf.getvalue(),
        )

    def test_extract_finish_price_low_price_field(self):
        p_data = {
            "Normal": {"marketPrice": 12.0, "lowPrice": 9.5},
            "Foil": {"marketPrice": 34.0, "lowPrice": 15.13},
        }
        self.assertEqual(extract_finish_price(p_data, "Standard", field="lowPrice"), 9.5)
        self.assertEqual(extract_finish_price(p_data, "Normal", field="lowPrice"), 9.5)
        self.assertEqual(extract_finish_price(p_data, "Foil", field="lowPrice"), 15.13)
        self.assertIsNone(extract_finish_price({"Foil": {"marketPrice": 10.0, "lowPrice": 0.0}}, "Foil", field="lowPrice"))

    def test_build_card_trajectory_populates_latest_day_low_price_only(self):
        dates = ["2026-11-13", "2026-11-14", "2026-11-15"]
        price_by_date = {
            "2026-11-13": {"714306": {"Foil": {"marketPrice": 107.55, "lowPrice": 99.00}}},
            "2026-11-14": {"714306": {"Foil": {"marketPrice": 107.03, "lowPrice": 99.50}}},
            "2026-11-15": {"714306": {"Foil": {"marketPrice": 107.03, "lowPrice": 98.80}}},
        }
        traj = _build_card_trajectory(
            pid=714306,
            name="V - Streetkid (ILegend)",
            display_pnum="B144",
            clean_pnum="144",
            rarity_code="IL",
            color="Red",
            card_type="Legend",
            valid_finish="Foil",
            owned_qty=1,
            expansion_name="Welcome to Night City - Beta",
            dates=dates,
            price_by_date=price_by_date,
            acquisitions={},
        )
        self.assertEqual(traj["currentPrice"], 107.03)
        self.assertEqual(traj["currentLowPrice"], 98.80)
        self.assertEqual(traj["minPrice"], 107.03)
        self.assertEqual(traj["maxPrice"], 107.55)

        # Missing lowPrice on the latest day must remain None without carrying forward prior days
        price_by_date["2026-11-15"]["714306"]["Foil"] = {"marketPrice": 107.03}
        traj_no_latest_low = _build_card_trajectory(
            pid=714306,
            name="V - Streetkid (ILegend)",
            display_pnum="B144",
            clean_pnum="144",
            rarity_code="IL",
            color="Red",
            card_type="Legend",
            valid_finish="Foil",
            owned_qty=1,
            expansion_name="Welcome to Night City - Beta",
            dates=dates,
            price_by_date=price_by_date,
            acquisitions={},
        )
        self.assertIsNone(traj_no_latest_low["currentLowPrice"])

        # Unpriced cards (hasPrice == False) must not expose an orphan lowPrice
        unpriced_by_date = {
            "2026-11-13": {},
            "2026-11-14": {},
            "2026-11-15": {"729825": {"Foil": {"marketPrice": None, "lowPrice": 199.99}}},
        }
        traj_unpriced = _build_card_trajectory(
            pid=729825,
            name="V - Streetkid (ILegend)",
            display_pnum="144",
            clean_pnum="144",
            rarity_code="IL",
            color="Red",
            card_type="Legend",
            valid_finish="Foil",
            owned_qty=0,
            expansion_name="Welcome to Night City - Retail",
            dates=dates,
            price_by_date=unpriced_by_date,
            acquisitions={},
        )
        self.assertFalse(traj_unpriced["hasPrice"])
        self.assertIsNone(traj_unpriced["currentLowPrice"])

    def test_generate_svg_chart_renders_low_price_endpoint_and_expands_y_axis(self):
        points = [("2026-11-06", 20.0), ("2026-11-09", 34.0), ("2026-11-15", 34.0)]
        svg = generate_svg_chart(points, card_id="n_719700", low_price=15.13)
        self.assertIn(">$34</text>", svg)
        self.assertIn(">$15</text>", svg)
        self.assertIn('class="low-price-connector"', svg)
        self.assertIn('class="low-price-dot"', svg)
        self.assertIn('data-low="true"', svg)
        self.assertIn('data-price="$15.13"', svg)
        self.assertIn('data-low-price="$15.13"', svg)

        # Overlapping lowPrice == marketPrice on latest-day acquisition omits .low-price-dot so gold acq core stays unoccluded
        svg_overlap = generate_svg_chart(
            points,
            card_id="n_719700",
            acq_info={"date": "2026-11-15", "cost": 34.0},
            low_price=34.0,
        )
        self.assertNotIn("low-price-connector", svg_overlap)
        self.assertNotIn('data-low="true"', svg_overlap)
        self.assertNotIn('class="low-price-dot"', svg_overlap)
        self.assertIn('data-acq="true" data-acq-cost="$34.00" data-low-price="$34.00"', svg_overlap)

        # Small non-zero gap (0 < gap < 5.0) on non-acquisition day uses standard r=3.0 violet dot (not r=6.5)
        svg_tiny_gap = generate_svg_chart(points, card_id="n_719700", low_price=33.50)
        self.assertNotIn("low-price-connector", svg_tiny_gap)
        self.assertNotIn('r="6.5"', svg_tiny_gap)
        self.assertIn('r="3.0" class="low-price-dot"', svg_tiny_gap)

        # Close-but-separate gap (5.0 <= gap < 8.0) renders dot and connector without overlapping hit target
        # For 20..34, y_range = 17.36 and plot_h = 82 => 1.3 price diff is ~6.1px
        svg_mid_gap = generate_svg_chart(points, card_id="n_719700", low_price=32.70)
        self.assertIn('class="low-price-connector"', svg_mid_gap)
        self.assertIn('class="low-price-dot" fill="#0d1117" stroke="#bc8cff" stroke-width="1.5" style="pointer-events: none;"', svg_mid_gap)
        self.assertNotIn('data-low="true"', svg_mid_gap)
        self.assertIn('data-low-price="$32.70"', svg_mid_gap)

        svg_no_low = generate_svg_chart(points, card_id="n_719700")
        self.assertIn(">$20</text>", svg_no_low)
        self.assertNotIn("low-price-connector", svg_no_low)

        # Initial SVG line color matches setAnchor() strict sign thresholds (>0, <0, ==0)
        self.assertIn('stroke="#3fb950"', generate_svg_chart([("2026-11-14", 100.0), ("2026-11-15", 100.5)], card_id="up"))
        self.assertIn('stroke="#f85149"', generate_svg_chart([("2026-11-14", 100.0), ("2026-11-15", 99.5)], card_id="dn"))
        self.assertIn('stroke="#58a6ff"', generate_svg_chart([("2026-11-14", 100.0), ("2026-11-15", 100.0)], card_id="fl"))

    def test_render_card_tile_shows_low_tooltip_and_preserves_acq_row(self):
        tile_html = _render_card_tile(
            {
                "productId": "714306",
                "name": "V - Streetkid (ILegend)",
                "printNumber": "B144",
                "rarity": "IL",
                "color": "Red",
                "cardType": "Legend",
                "currentPrice": 107.03,
                "currentLowPrice": 98.80,
                "hasPrice": True,
                "ownedQty": 1,
                "change7d": -5.40,
                "change7dPct": -4.8,
                "changeAll": 27.54,
                "changeAllPct": 34.6,
                "hasAcquisition": True,
                "acqDate": "2026-10-13",
                "acqCost": 125.56,
                "acqDeltaDollar": -18.53,
                "acqDeltaPct": -14.8,
                "svgChart": "<svg></svg>",
            },
            edition_name="beta",
            initial_display="flex",
        )
        self.assertIn('class="price-main price-low-tooltip"', tile_html)
        self.assertIn('data-tooltip="Current TCG Low (excl. shipping): $98.80"', tile_html)
        self.assertNotIn("price-low-badge", tile_html)
        self.assertIn("Acq base:", tile_html)
        self.assertIn("$125.56", tile_html)
        self.assertIn("-$18.53 (-14.8%)", tile_html)

        # Unpriced or missing-low tile keeps plain .price-main without tooltip
        unpriced_tile = _render_card_tile(
            {
                "productId": "714306",
                "name": "V - Streetkid (ILegend)",
                "printNumber": "B144",
                "rarity": "IL",
                "color": "Red",
                "cardType": "Legend",
                "currentPrice": 0.0,
                "currentLowPrice": None,
                "hasPrice": False,
                "ownedQty": 0,
                "change7d": 0.0,
                "change7dPct": 0.0,
                "changeAll": 0.0,
                "changeAllPct": 0.0,
                "hasAcquisition": False,
                "svgChart": "<svg></svg>",
            },
            edition_name="beta",
            initial_display="flex",
        )
        self.assertIn('<span class="price-main">N/A</span>', unpriced_tile)
        self.assertNotIn("price-low-tooltip", unpriced_tile)


if __name__ == "__main__":
    unittest.main()
