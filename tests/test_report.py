"""
Automated unit tests for portfolio markdown report generation.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tracker.report import generate_portfolio_report
from tracker.valuation import init_database
from tests.fixtures import seed_metadata, seed_snapshots, seed_summary


class TestReportGeneration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.db_path = str(self.test_dir / "test_report.db")
        self.output_md = str(self.test_dir / "TEST_REPORT.md")

        conn = init_database(self.db_path)
        seed_metadata(conn, [
            {
                "card_key": "Exp::001::Standard",
                "product_id": 101,
                "name": "Johnny Silverhand",
                "print_number": "001",
                "expansion": "Welcome to Night City - Beta",
                "finish": "Standard",
                "rarity": "Iconic",
                "color": "Red",
                "first_seen_date": "2026-09-11",
                "baseline_market_price": 15.00,
                "item_type": "Card",
            },
            {
                "card_key": "SEALED::Welcome to Night City - Beta::714346::2026-09-11",
                "product_id": 714346,
                "name": "Welcome to Night City - Beta Booster Box",
                "print_number": None,
                "expansion": "Welcome to Night City - Beta",
                "finish": "Standard",
                "rarity": "Sealed",
                "color": "",
                "first_seen_date": "2026-09-11",
                "baseline_market_price": 216.08,
                "item_type": "Sealed",
            },
        ])
        seed_snapshots(conn, "2026-09-14", [
            {
                "card_key": "Exp::001::Standard",
                "quantity": 1,
                "unit_market_price": 20.00,
                "unit_low_price": 18.00,
                "unit_mid_price": 20.00,
                "unit_high_price": 25.00,
                "line_total": 20.00,
                "baseline_price": 15.00,
                "lifetime_gain_dollar": 5.00,
                "lifetime_gain_pct": 33.33,
            },
            {
                "card_key": "SEALED::Welcome to Night City - Beta::714346::2026-09-11",
                "quantity": 1,
                "unit_market_price": 235.17,
                "unit_low_price": 230.00,
                "unit_mid_price": 235.00,
                "unit_high_price": 250.00,
                "line_total": 235.17,
                "baseline_price": 216.08,
                "lifetime_gain_dollar": 19.09,
                "lifetime_gain_pct": 8.83,
            },
        ])
        seed_summary(
            conn,
            "2026-09-14",
            total_value=255.17,
            total_cards=1,
            total_sealed=1,
            lifetime_gain=24.09,
            cost_basis=0.0,
            collection_source="—",
        )
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_report_isolates_sealed_products(self):
        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        self.assertTrue(os.path.exists(self.output_md))

        content = Path(self.output_md).read_text(encoding="utf-8")

        # Executive summary contains total sealed units
        self.assertIn("**Total Sealed Items** | **1** unit", content)
        self.assertIn("**Total Physical Cards** | **1** copies", content)

        # Dedicated sealed section exists with Acquired column under Portfolio Breakdown
        portfolio_section = content.split("## 📊 Portfolio Breakdown")[1].split("## 📈 Top Gainers")[0]
        self.assertIn("### Sealed Products", portfolio_section)
        self.assertIn("Welcome to Night City - Beta Booster Box", portfolio_section)
        self.assertIn("`2026-09-11`", portfolio_section)
        self.assertIn("$235.17", portfolio_section)

        # Rarity breakdown does NOT include "Sealed"
        self.assertNotIn("Sealed", content.split("### Rarity")[1].split("### Color")[0])

        # High-Value Singles does NOT contain the booster box
        singles_section = content.split("### High-Value Singles")[1].split("### Sealed Products")[0]
        self.assertIn("Johnny Silverhand", singles_section)
        self.assertNotIn("Booster Box", singles_section)

        # Top Gainers does NOT contain the booster box
        gainers_section = content.split("## 📈 Top Gainers")[1].split("## 📉 Top Decliners")[0]
        self.assertIn("Johnny Silverhand", gainers_section)
        self.assertNotIn("Booster Box", gainers_section)

    def test_report_header_timestamps(self):
        # Verify header labels and clean formatting
        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")
        self.assertIn("**Collection Last Updated**: `2026-09-14`", content)
        self.assertIn("**Collection Source**: `—`", content)
        self.assertIn("**Prices Last Updated**: `2026-09-14`", content)
        self.assertIn("**Report Generated**:", content)
        self.assertNotIn("**Portfolio Last Updated**:", content)
        self.assertNotIn("**Snapshot Date**:", content)
        self.assertNotIn("**Last Updated**:", content)
        # Ensure verbose seconds and timezone string are excluded
        self.assertNotIn("Pacific Daylight Time", content)
        self.assertNotIn("PDT", content)

        # Verify ordering: Collection Last Updated before Collection Source before Prices Last Updated before Report Generated
        idx_collection = content.index("**Collection Last Updated**:")
        idx_source = content.index("**Collection Source**:")
        idx_prices = content.index("**Prices Last Updated**:")
        idx_report = content.index("**Report Generated**:")
        self.assertTrue(idx_collection < idx_source < idx_prices < idx_report)

    def test_report_custom_collection_source(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(portfolio_daily_summary)")
            cols = [c[1] for c in cur.fetchall()]
            if "collection_source" not in cols:
                cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN collection_source TEXT")
            cur.execute("UPDATE portfolio_daily_summary SET collection_source = 'CardNexus API' WHERE date = '2026-09-14'")
            conn.commit()
        finally:
            conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")
        self.assertIn("**Collection Source**: `CardNexus API`", content)

    def test_report_expansion_breakdown(self):
        conn = sqlite3.connect(self.db_path)
        try:
            # Add a card from a second expansion: Cyberpunk Edgerunners
            seed_metadata(conn, [{
                "card_key": "Exp2::002::Standard",
                "product_id": 102,
                "name": "David Martinez",
                "print_number": "002",
                "expansion": "Cyberpunk Edgerunners",
                "finish": "Standard",
                "rarity": "Rare",
                "color": "Yellow",
                "first_seen_date": "2026-09-11",
                "baseline_market_price": 50.00,
                "item_type": "Card",
            }])
            seed_snapshots(conn, "2026-09-14", [{
                "card_key": "Exp2::002::Standard",
                "quantity": 2,
                "unit_market_price": 60.00,
                "unit_low_price": 55.00,
                "unit_mid_price": 60.00,
                "unit_high_price": 70.00,
                "line_total": 120.00,
                "baseline_price": 50.00,
                "lifetime_gain_dollar": 10.00,
                "lifetime_gain_pct": 20.00,
            }])
            # Update summary total_val to reflect the added card: 255.17 + 120.00 = 375.17
            cur = conn.cursor()
            cur.execute("UPDATE portfolio_daily_summary SET total_value = 375.17, total_cards = 3, unique_items = 3 WHERE date = '2026-09-14'")
            conn.commit()
        finally:
            conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")

        self.assertIn("### Expansion", content)
        self.assertIn("| Expansion | Unique Items | Physical Copies | Market Value | % of Portfolio |", content)

        # Edgerunners is $120.00 (32.0%), Welcome to Night City - Beta is $20.00 (5.3%)
        self.assertIn("| **Cyberpunk Edgerunners** | 1 | 2 | `$120.00` | 32.0% |", content)
        self.assertIn("| **Welcome to Night City - Beta** | 1 | 1 | `$20.00` | 5.3% |", content)

        # Confirm sorted order: Cyberpunk Edgerunners should appear before Welcome to Night City - Beta
        idx_edge = content.index("**Cyberpunk Edgerunners**")
        idx_wtnc = content.index("**Welcome to Night City - Beta**")
        self.assertTrue(idx_edge < idx_wtnc)

    def test_report_custom_collection_updated_at(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(portfolio_daily_summary)")
            cols = [c[1] for c in cur.fetchall()]
            if "collection_updated_at" not in cols:
                cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN collection_updated_at TEXT")
            cur.execute("UPDATE portfolio_daily_summary SET collection_updated_at = '2026-09-14 03:45 PM' WHERE date = '2026-09-14'")
            conn.commit()
        finally:
            conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")
        self.assertIn("**Collection Last Updated**: `2026-09-14 03:45 PM`", content)
        self.assertIn("**Prices Last Updated**: `2026-09-14`", content)
        self.assertIn("**Report Generated**:", content)

    def test_report_target_date_historical(self):
        # Insert historical 2026-09-13 record
        conn = sqlite3.connect(self.db_path)
        seed_summary(
            conn,
            "2026-09-13",
            total_value=200.00,
            total_cards=1,
            unique_items=1,
            lifetime_gain=10.00,
            lifetime_pct=5.00,
        )
        seed_snapshots(conn, "2026-09-13", [{
            "card_key": "Exp::001::Standard",
            "quantity": 1,
            "unit_market_price": 15.00,
            "unit_low_price": 15.00,
            "unit_mid_price": 15.00,
            "unit_high_price": 20.00,
            "line_total": 15.00,
            "baseline_price": 15.00,
            "lifetime_gain_dollar": 0.00,
            "lifetime_gain_pct": 0.00,
        }])
        conn.close()

        # Generate report specifically for 2026-09-13 even though 2026-09-14 exists
        generate_portfolio_report(
            db_path=self.db_path,
            output_md=self.output_md,
            target_date="2026-09-13",
        )
        content = Path(self.output_md).read_text(encoding="utf-8")
        self.assertIn("**Collection Last Updated**: `2026-09-13", content)
        self.assertIn("**Prices Last Updated**: `2026-09-13", content)
        self.assertIn("**Report Generated**:", content)
        self.assertIn("$200.00", content)

    def test_report_omits_l7d_when_no_prior_history(self):
        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")
        self.assertNotIn("### L7D (Since", content)
        self.assertIn("## 📈 Top Gainers", content)
        self.assertIn("### Lifetime", content)
        self.assertIn("## 📉 Top Decliners", content)

    def test_report_l7d_gainers_and_decliners(self):
        conn = sqlite3.connect(self.db_path)
        seed_metadata(conn, [{
            "card_key": "Exp::002::Standard",
            "product_id": 102,
            "name": "V - Nomad",
            "print_number": "002",
            "expansion": "Welcome to Night City - Beta",
            "finish": "Standard",
            "rarity": "Rare",
            "color": "Yellow",
            "first_seen_date": "2026-09-07",
            "baseline_market_price": 30.00,
            "item_type": "Card",
        }])
        seed_summary(
            conn,
            "2026-09-07",
            total_value=245.00,
            total_cards=2,
            total_sealed=1,
            unique_items=3,
        )
        seed_snapshots(conn, "2026-09-07", [
            {"card_key": "Exp::001::Standard", "quantity": 1, "unit_market_price": 15.00, "line_total": 15.00, "baseline_price": 15.00},
            {"card_key": "Exp::002::Standard", "quantity": 1, "unit_market_price": 30.00, "line_total": 30.00, "baseline_price": 30.00},
            {"card_key": "SEALED::Welcome to Night City - Beta::714346::2026-09-11", "quantity": 1, "unit_market_price": 200.00, "line_total": 200.00, "baseline_price": 200.00},
        ])
        seed_snapshots(conn, "2026-09-14", [
            {"card_key": "Exp::002::Standard", "quantity": 1, "unit_market_price": 20.00, "unit_low_price": 18.00, "unit_mid_price": 20.00, "unit_high_price": 25.00, "line_total": 20.00, "baseline_price": 30.00, "lifetime_gain_dollar": -10.00, "lifetime_gain_pct": -33.33},
        ])
        conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")

        # Verify Top Gainers primary section and subsections
        self.assertIn("## 📈 Top Gainers", content)
        self.assertIn("### Lifetime", content)
        self.assertIn("### L7D (Since `2026-09-07`)", content)
        gainers_l7d = content.split("### L7D (Since `2026-09-07`)")[1].split("## 📉 Top Decliners")[0]
        self.assertIn("Johnny Silverhand", gainers_l7d)
        self.assertIn("$20.00", gainers_l7d)
        self.assertIn("$15.00", gainers_l7d)
        self.assertIn("+$5.00", gainers_l7d)
        self.assertIn("+33.3%", gainers_l7d)
        self.assertNotIn("Booster Box", gainers_l7d)

        # Verify Top Decliners primary section and subsections
        self.assertIn("## 📉 Top Decliners", content)
        decliners_l7d = content.split("## 📉 Top Decliners")[1].split("### L7D (Since `2026-09-07`)")[1]
        self.assertIn("V - Nomad", decliners_l7d)
        self.assertIn("$20.00", decliners_l7d)
        self.assertIn("$30.00", decliners_l7d)
        self.assertIn("$-10.00", decliners_l7d)
        self.assertIn("-33.3%", decliners_l7d)
        self.assertNotIn("Booster Box", decliners_l7d)

    def test_report_color_null_and_empty_string_safety(self):
        conn = sqlite3.connect(self.db_path)
        seed_metadata(conn, [{
            "card_key": "Exp::003::Standard",
            "product_id": 103,
            "name": "Uncolored Card",
            "print_number": "003",
            "expansion": "Welcome to Night City - Beta",
            "finish": "Standard",
            "rarity": "Common",
            "color": "",
            "first_seen_date": "2026-09-14",
            "baseline_market_price": 5.00,
            "item_type": "Card",
        }])
        seed_snapshots(conn, "2026-09-14", [{
            "card_key": "Exp::003::Standard",
            "quantity": 1,
            "unit_market_price": 5.00,
            "line_total": 5.00,
            "baseline_price": 5.00,
        }])
        conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")

        self.assertIn("### Color", content)
        self.assertNotIn("****", content)
        self.assertIn("| **Unknown** | 1 | 1 | `$5.00` |", content)

    def test_multi_lot_high_value_cards_aggregated(self):
        conn = sqlite3.connect(self.db_path)
        seed_metadata(conn, [
            {"card_key": "Exp::B034::Standard::2026-09-02", "product_id": 500, "name": "Towerfall", "print_number": "B034", "expansion": "Welcome to Night City - Beta", "finish": "Standard", "rarity": "Rare", "color": "Blue", "first_seen_date": "2026-09-02", "baseline_market_price": 10.00, "item_type": "Card"},
            {"card_key": "Exp::B034::Standard::2026-09-18", "product_id": 500, "name": "Towerfall", "print_number": "B034", "expansion": "Welcome to Night City - Beta", "finish": "Standard", "rarity": "Rare", "color": "Blue", "first_seen_date": "2026-09-18", "baseline_market_price": 15.00, "item_type": "Card"},
        ])
        seed_snapshots(conn, "2026-09-18", [
            {"card_key": "Exp::B034::Standard::2026-09-02", "quantity": 1, "unit_market_price": 15.00, "unit_low_price": 15.00, "unit_mid_price": 15.00, "unit_high_price": 18.00, "line_total": 15.00, "baseline_price": 10.00, "lifetime_gain_dollar": 5.00, "lifetime_gain_pct": 50.0},
            {"card_key": "Exp::B034::Standard::2026-09-18", "quantity": 2, "unit_market_price": 15.00, "unit_low_price": 15.00, "unit_mid_price": 15.00, "unit_high_price": 18.00, "line_total": 30.00, "baseline_price": 15.00, "lifetime_gain_dollar": 0.00, "lifetime_gain_pct": 0.0},
        ])
        seed_summary(
            conn,
            "2026-09-18",
            total_value=45.00,
            total_cards=3,
            unique_items=1,
            lifetime_gain=5.00,
            lifetime_pct=12.5,
        )
        conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md, target_date="2026-09-18")
        content = Path(self.output_md).read_text(encoding="utf-8")

        # In High-Value Singles table, Towerfall should appear only once with aggregated quantity 3 and line total $45.00
        singles_section = content.split("### High-Value Singles")[1]
        if "### Sealed Products" in singles_section:
            singles_section = singles_section.split("### Sealed Products")[0]
        else:
            singles_section = singles_section.split("## 📈 Top Gainers")[0]
        self.assertEqual(singles_section.count("Towerfall"), 1)
        self.assertIn("#### Non-Iconic/Nova Rare Singles", singles_section)
        self.assertIn("| 🔵 **Towerfall** | Unknown | Welcome to Night City - Beta | **◇ Rare** | Standard | 3 | `$15.00` | `$45.00` |", singles_section)

    def test_multi_lot_rarity_breakdown_distinct_card_count(self):
        conn = sqlite3.connect(self.db_path)
        seed_metadata(conn, [
            {"card_key": "Exp::B034::Standard::2026-09-02", "product_id": 500, "name": "Towerfall", "print_number": "B034", "expansion": "Welcome to Night City - Beta", "finish": "Standard", "rarity": "Rare", "color": "Blue", "first_seen_date": "2026-09-02", "baseline_market_price": 10.00, "item_type": "Card"},
            {"card_key": "Exp::B034::Standard::2026-09-18", "product_id": 500, "name": "Towerfall", "print_number": "B034", "expansion": "Welcome to Night City - Beta", "finish": "Standard", "rarity": "Rare", "color": "Blue", "first_seen_date": "2026-09-18", "baseline_market_price": 15.00, "item_type": "Card"},
        ])
        seed_snapshots(conn, "2026-09-18", [
            {"card_key": "Exp::B034::Standard::2026-09-02", "quantity": 1, "unit_market_price": 15.00, "unit_low_price": 15.00, "unit_mid_price": 15.00, "unit_high_price": 18.00, "line_total": 15.00, "baseline_price": 10.00, "lifetime_gain_dollar": 5.00, "lifetime_gain_pct": 50.0},
            {"card_key": "Exp::B034::Standard::2026-09-18", "quantity": 2, "unit_market_price": 15.00, "unit_low_price": 15.00, "unit_mid_price": 15.00, "unit_high_price": 18.00, "line_total": 30.00, "baseline_price": 15.00, "lifetime_gain_dollar": 0.00, "lifetime_gain_pct": 0.0},
        ])
        seed_summary(
            conn,
            "2026-09-18",
            total_value=45.00,
            total_cards=3,
            unique_items=1,
            lifetime_gain=5.00,
            lifetime_pct=12.5,
        )
        conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md, target_date="2026-09-18")
        content = Path(self.output_md).read_text(encoding="utf-8")

        # Rarity breakdown should count 1 distinct card and 3 copies for Rare
        rarity_section = content.split("### Rarity")[1].split("### Color")[0]
        self.assertIn("| **◇ Rare** | 1 | 3 | `$45.00` |", rarity_section)

    def test_top_gainers_ranks_by_per_unit_delta_and_includes_acquired_date(self):
        conn = sqlite3.connect(self.db_path)
        # Card A: 1 copy, unit price 20.00, baseline 10.00 -> +10.00/unit, total gain $10.00
        # Card B: 2 copies, unit price 20.00, baseline 12.00 -> +8.00/unit, total gain $16.00
        seed_metadata(conn, [
            {"card_key": "Exp::A::Standard::2026-09-02", "product_id": 601, "name": "Card Alpha", "print_number": "A", "expansion": "Expansion A", "finish": "Standard", "rarity": "Rare", "color": "Red", "first_seen_date": "2026-09-02", "baseline_market_price": 10.00, "item_type": "Card"},
            {"card_key": "Exp::B::Standard::2026-09-10", "product_id": 602, "name": "Card Beta", "print_number": "B", "expansion": "Expansion A", "finish": "Standard", "rarity": "Rare", "color": "Blue", "first_seen_date": "2026-09-10", "baseline_market_price": 12.00, "item_type": "Card"},
        ])
        seed_snapshots(conn, "2026-09-19", [
            {"card_key": "Exp::A::Standard::2026-09-02", "quantity": 1, "unit_market_price": 20.00, "unit_low_price": 20.00, "unit_mid_price": 20.00, "unit_high_price": 20.00, "line_total": 20.00, "baseline_price": 10.00, "lifetime_gain_dollar": 10.00, "lifetime_gain_pct": 100.0},
            {"card_key": "Exp::B::Standard::2026-09-10", "quantity": 2, "unit_market_price": 20.00, "unit_low_price": 20.00, "unit_mid_price": 20.00, "unit_high_price": 20.00, "line_total": 40.00, "baseline_price": 12.00, "lifetime_gain_dollar": 16.00, "lifetime_gain_pct": 66.7},
        ])
        seed_summary(
            conn,
            "2026-09-19",
            total_value=60.00,
            total_cards=3,
            unique_items=2,
            lifetime_gain=26.00,
            lifetime_pct=76.5,
        )
        conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md, target_date="2026-09-19")
        content = Path(self.output_md).read_text(encoding="utf-8")

        gainers_section = content.split("## 📈 Top Gainers")[1].split("## 📉 Top Decliners")[0]

        # Table header must include Acquired
        self.assertIn("| Card Name | Acquired | Rarity | Finish | Qty | Unit Price | Baseline Price | Dollar Gain | Percent Gain |", gainers_section)

        # Card Alpha (+$10.00/unit) must rank BEFORE Card Beta (+$8.00/unit, despite higher $16 position gain)
        idx_alpha = gainers_section.find("Card Alpha")
        idx_beta = gainers_section.find("Card Beta")
        self.assertNotEqual(idx_alpha, -1)
        self.assertNotEqual(idx_beta, -1)
        self.assertLess(idx_alpha, idx_beta)

        # Acquisition dates must appear in the rows
        self.assertIn("| 🔴 **Card Alpha** | `2026-09-02` |", gainers_section)
        self.assertIn("| 🔵 **Card Beta** | `2026-09-10` |", gainers_section)

    def test_high_value_singles_subsections_and_card_types(self):
        conn = sqlite3.connect(self.db_path)
        seed_metadata(conn, [
            {"card_key": "Exp::001::Foil::2026-09-02", "product_id": 801, "name": "Hanako Iconic", "print_number": "B141", "expansion": "Welcome to Night City - Beta", "finish": "Foil", "rarity": "Iconic Legend", "color": "Green", "first_seen_date": "2026-09-02", "baseline_market_price": 40.00, "item_type": "Card", "card_type": "Legend"},
            {"card_key": "Exp::002::Foil::2026-09-02", "product_id": 802, "name": "Mantis Nova", "print_number": "008", "expansion": "Welcome to Night City - Beta", "finish": "Foil", "rarity": "Nova Rare", "color": "Red", "first_seen_date": "2026-09-02", "baseline_market_price": 25.00, "item_type": "Card", "card_type": "Gear"},
            {"card_key": "Exp::003::Foil::2026-09-02", "product_id": 803, "name": "Jackie Epic", "print_number": "B050", "expansion": "Welcome to Night City - Beta", "finish": "Foil", "rarity": "Epic", "color": "Blue", "first_seen_date": "2026-09-02", "baseline_market_price": 10.00, "item_type": "Card", "card_type": "Unit"},
        ])
        seed_snapshots(conn, "2026-09-20", [
            {"card_key": "Exp::001::Foil::2026-09-02", "quantity": 1, "unit_market_price": 50.00, "unit_low_price": 50.00, "unit_mid_price": 50.00, "unit_high_price": 50.00, "line_total": 50.00, "baseline_price": 40.00, "lifetime_gain_dollar": 10.00, "lifetime_gain_pct": 25.0},
            {"card_key": "Exp::002::Foil::2026-09-02", "quantity": 1, "unit_market_price": 30.00, "unit_low_price": 30.00, "unit_mid_price": 30.00, "unit_high_price": 30.00, "line_total": 30.00, "baseline_price": 25.00, "lifetime_gain_dollar": 5.00, "lifetime_gain_pct": 20.0},
            {"card_key": "Exp::003::Foil::2026-09-02", "quantity": 1, "unit_market_price": 15.00, "unit_low_price": 15.00, "unit_mid_price": 15.00, "unit_high_price": 15.00, "line_total": 15.00, "baseline_price": 10.00, "lifetime_gain_dollar": 5.00, "lifetime_gain_pct": 50.0},
        ])
        seed_summary(
            conn,
            "2026-09-20",
            total_value=95.00,
            total_cards=3,
            unique_items=3,
            lifetime_gain=20.00,
            lifetime_pct=26.7,
        )
        conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md, target_date="2026-09-20")
        content = Path(self.output_md).read_text(encoding="utf-8")

        # Card Type breakdown table assertion
        self.assertIn("### Card Type", content)
        self.assertIn("| **Legend** | 1 | 1 | `$50.00` | 52.6% |", content)
        self.assertIn("| **Gear** | 1 | 1 | `$30.00` | 31.6% |", content)
        self.assertIn("| **Unit** | 1 | 1 | `$15.00` | 15.8% |", content)

        # High-Value Singles partitioned subsections assertion
        singles_section = content.split("### High-Value Singles")[1].split("## 📈 Top Gainers")[0]
        self.assertIn("#### Iconic Singles", singles_section)
        self.assertIn("| 🟢 **Hanako Iconic** | Legend | Welcome to Night City - Beta | **★ Iconic** | Foil | 1 | `$50.00` | `$50.00` |", singles_section)

        self.assertIn("#### Nova Rare Singles", singles_section)
        self.assertIn("| 🔴 **Mantis Nova** | Gear | Welcome to Night City - Beta | **▣ Nova** | Foil | 1 | `$30.00` | `$30.00` |", singles_section)

        self.assertIn("#### Non-Iconic/Nova Rare Singles", singles_section)
        self.assertIn("| 🔵 **Jackie Epic** | Unit | Welcome to Night City - Beta | **◈ Epic** | Foil | 1 | `$15.00` | `$15.00` |", singles_section)


    def test_report_header_with_purchases_updated_at(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA table_info(portfolio_daily_summary)")
            cols = [c[1] for c in cur.fetchall()]
            if "total_cost_basis" not in cols:
                cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN total_cost_basis REAL DEFAULT 0.0")
            if "purchases_updated_at" not in cols:
                cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN purchases_updated_at TEXT")
            cur.execute("""
                UPDATE portfolio_daily_summary 
                SET total_cost_basis = 489.21,
                    purchases_updated_at = '2026-09-14 04:00 PM'
                WHERE date = '2026-09-14'
            """)
            conn.commit()
        finally:
            conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")
        self.assertIn("**Collection Last Updated**:", content)
        self.assertIn("**Collection Source**:", content)
        self.assertIn("**Prices Last Updated**:", content)
        self.assertIn("**Purchases Last Updated**: `2026-09-14 04:00 PM`", content)
        self.assertIn("**Report Generated**:", content)

        idx_collection = content.index("**Collection Last Updated**:")
        idx_source = content.index("**Collection Source**:")
        idx_prices = content.index("**Prices Last Updated**:")
        idx_purchases = content.index("**Purchases Last Updated**:")
        idx_report = content.index("**Report Generated**:")
        self.assertTrue(idx_collection < idx_source < idx_prices < idx_purchases < idx_report)

    def test_report_header_omits_purchases_when_cost_basis_zero(self):
        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")
        self.assertNotIn("**Purchases Last Updated**:", content)

    def test_report_numerical_roi_and_net_return(self):
        conn = sqlite3.connect(self.db_path)
        try:
            seed_summary(
                conn,
                "2026-09-14",
                total_value=1250.00,
                total_cards=3,
                cost_basis=1000.00,
                net_unrealized_gain=250.00,
                net_unrealized_pct=25.00,
            )
        finally:
            conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")

        self.assertIn("| **Total Portfolio Market Value** | **`$1,250.00`** |", content)
        self.assertIn("| **Total Invested Cost Basis** | **`$1,000.00`** |", content)
        self.assertIn("| **Net Unrealized Gain / Loss** | **+$250.00** (+25.00%) |", content)

    def test_report_rolling_l7d_delta_accuracy(self):
        conn = sqlite3.connect(self.db_path)
        try:
            seed_summary(
                conn,
                "2026-09-14",
                total_value=38.00,
                total_cards=3,
                l7d_dollar_delta=250.00,
                l7d_pct_delta=25.00,
            )
        finally:
            conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")

        self.assertIn("| **Rolling L7D Performance** | **+$250.00** (+25.00%) |", content)

    def test_report_high_value_singles_boundary_conditions(self):
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM daily_snapshots WHERE date = '2026-09-14'")
            cur.execute("DELETE FROM card_metadata")

            seed_metadata(conn, [
                {"card_key": "C::001", "name": "Under Threshold Card", "expansion": "NC", "finish": "Standard", "rarity": "Common", "item_type": "Card", "print_number": "001"},
                {"card_key": "C::002", "name": "Exact Boundary Card", "expansion": "NC", "finish": "Standard", "rarity": "Rare", "item_type": "Card", "print_number": "002"},
                {"card_key": "C::003", "name": "Well Over Threshold Card", "expansion": "NC", "finish": "Standard", "rarity": "Epic", "item_type": "Card", "print_number": "003"},
            ])
            seed_snapshots(conn, "2026-09-14", [
                {"card_key": "C::001", "quantity": 1, "unit_market_price": 9.99, "line_total": 9.99},
                {"card_key": "C::002", "quantity": 1, "unit_market_price": 10.00, "line_total": 10.00},
                {"card_key": "C::003", "quantity": 1, "unit_market_price": 25.00, "line_total": 25.00},
            ])
            seed_summary(conn, "2026-09-14", total_value=44.99, total_cards=3)
        finally:
            conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")

        self.assertIn("### High-Value Singles", content)
        # $9.99 must be strictly excluded
        self.assertNotIn("Under Threshold Card", content)
        # $10.00 must be included (boundary condition inclusive >= 10.0)
        self.assertIn("Exact Boundary Card", content)
        self.assertIn("`$10.00`", content)
        # $25.00 must be included
        self.assertIn("Well Over Threshold Card", content)
        self.assertIn("`$25.00`", content)
        # $25.00 must appear before $10.00 in high value table (sorted descending)
        idx_25 = content.index("Well Over Threshold Card")
        idx_10 = content.index("Exact Boundary Card")
        self.assertTrue(idx_25 < idx_10)


if __name__ == "__main__":
    unittest.main()
