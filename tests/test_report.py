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

        # Dedicated sealed section exists with Acquired and % of Portfolio Value columns (without Expansion)
        portfolio_section = content.split("## 📊 Portfolio Breakdown")[1].split("## 📈 Top Gainers")[0]
        self.assertIn("### Sealed Products", portfolio_section)
        self.assertIn("| Product Name | Acquired | Qty | Unit Price | Total Value | % of Portfolio Value | Baseline Price | Lifetime Gain |", portfolio_section)
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
        self.assertIn("| Expansion | Unique Items | Physical Copies | % of Cards | Market Value | % of Card Value |", content)

        # Edgerunners is 2 copies (66.7%), $120.00 (85.7% of singles value); Welcome to Night City - Beta is 1 copy (33.3%), $20.00 (14.3% of singles value)
        self.assertIn("| **Cyberpunk Edgerunners** | 1 | 2 | 66.7% | `$120.00` | 85.7% |", content)
        self.assertIn("| **Welcome to Night City - Beta** | 1 | 1 | 33.3% | `$20.00` | 14.3% |", content)

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
        self.assertIn("| **Unknown** | 1 | 1 | 100.0% | `$5.00` |", content)

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
        self.assertIn("| **◇ Rare** | 1 | 3 | 100.0% | `$45.00` |", rarity_section)

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
        self.assertIn("| **Legend** | 1 | 1 | 33.3% | `$50.00` | 52.6% |", content)
        self.assertIn("| **Gear** | 1 | 1 | 33.3% | `$30.00` | 31.6% |", content)
        self.assertIn("| **Unit** | 1 | 1 | 33.3% | `$15.00` | 15.8% |", content)

        # High-Value Singles partitioned subsections assertion
        singles_section = content.split("### High-Value Singles")[1].split("## 📈 Top Gainers")[0]
        self.assertIn("#### Iconic Singles", singles_section)
        self.assertIn("| 🟢 **Hanako Iconic** | Legend | Welcome to Night City - Beta | **★ Iconic** | Foil | 1 | `$50.00` | `$50.00` |", singles_section)

        self.assertIn("#### Nova Rare Singles", singles_section)
        self.assertIn("| 🔴 **Mantis Nova** | Gear | Welcome to Night City - Beta | **▣ Nova** | Foil | 1 | `$30.00` | `$30.00` |", singles_section)

        self.assertIn("#### Non-Iconic/Nova Rare Singles", singles_section)
        self.assertIn("| 🔵 **Jackie Epic** | Unit | Welcome to Night City - Beta | **◈ Epic** | Foil | 1 | `$15.00` | `$15.00` |", singles_section)

    def test_high_value_singles_shorthand_rarity_partitioning(self):
        conn = sqlite3.connect(self.db_path)
        seed_metadata(conn, [
            {"card_key": "Exp::001::Foil::2026-09-02", "product_id": 901, "name": "Royce", "print_number": "B007", "expansion": "Welcome to Night City - Beta", "finish": "Foil", "rarity": "IL", "color": "Red", "first_seen_date": "2026-09-02", "baseline_market_price": 60.00, "item_type": "Card", "card_type": "Legend"},
            {"card_key": "Exp::002::Foil::2026-09-02", "product_id": 902, "name": "Alt Cunningham", "print_number": "B052", "expansion": "Welcome to Night City - Beta", "finish": "Foil", "rarity": "IO", "color": "Yellow", "first_seen_date": "2026-09-02", "baseline_market_price": 15.00, "item_type": "Card", "card_type": "Unit"},
            {"card_key": "Exp::003::Foil::2026-09-02", "product_id": 903, "name": "Jackie Box Topper", "print_number": "B003", "expansion": "Box Toppers - Beta", "finish": "Foil", "rarity": "NR", "color": "Blue", "first_seen_date": "2026-09-02", "baseline_market_price": 40.00, "item_type": "Card", "card_type": "Legend"},
            {"card_key": "Exp::004::Foil::2026-09-02", "product_id": 904, "name": "Johnny Secret", "print_number": "B001", "expansion": "Welcome to Night City - Beta", "finish": "Foil", "rarity": "S", "color": "Red", "first_seen_date": "2026-09-02", "baseline_market_price": 25.00, "item_type": "Card", "card_type": "Legend"},
            {"card_key": "Exp::005::Standard::2026-09-02", "product_id": 905, "name": "Flathead", "print_number": "B015", "expansion": "The Heist - Beta Starter Deck", "finish": "Standard", "rarity": "UC", "color": "Blue", "first_seen_date": "2026-09-02", "baseline_market_price": 12.00, "item_type": "Card", "card_type": "Unit"},
            {"card_key": "Exp::006::Foil::2026-09-02", "product_id": 906, "name": "Towerfall", "print_number": "B034", "expansion": "Welcome to Night City - Beta", "finish": "Foil", "rarity": "E", "color": "Blue", "first_seen_date": "2026-09-02", "baseline_market_price": 10.00, "item_type": "Card", "card_type": "Program"},
        ])
        seed_snapshots(conn, "2026-09-25", [
            {"card_key": "Exp::001::Foil::2026-09-02", "quantity": 1, "unit_market_price": 67.89, "unit_low_price": 60.00, "unit_mid_price": 67.89, "unit_high_price": 70.00, "line_total": 67.89, "baseline_price": 60.00, "lifetime_gain_dollar": 7.89, "lifetime_gain_pct": 13.15},
            {"card_key": "Exp::002::Foil::2026-09-02", "quantity": 1, "unit_market_price": 17.46, "unit_low_price": 15.00, "unit_mid_price": 17.46, "unit_high_price": 20.00, "line_total": 17.46, "baseline_price": 15.00, "lifetime_gain_dollar": 2.46, "lifetime_gain_pct": 16.4},
            {"card_key": "Exp::003::Foil::2026-09-02", "quantity": 1, "unit_market_price": 41.49, "unit_low_price": 40.00, "unit_mid_price": 41.49, "unit_high_price": 45.00, "line_total": 41.49, "baseline_price": 40.00, "lifetime_gain_dollar": 1.49, "lifetime_gain_pct": 3.73},
            {"card_key": "Exp::004::Foil::2026-09-02", "quantity": 1, "unit_market_price": 28.18, "unit_low_price": 25.00, "unit_mid_price": 28.18, "unit_high_price": 30.00, "line_total": 28.18, "baseline_price": 25.00, "lifetime_gain_dollar": 3.18, "lifetime_gain_pct": 12.72},
            {"card_key": "Exp::005::Standard::2026-09-02", "quantity": 1, "unit_market_price": 12.36, "unit_low_price": 10.00, "unit_mid_price": 12.36, "unit_high_price": 15.00, "line_total": 12.36, "baseline_price": 12.00, "lifetime_gain_dollar": 0.36, "lifetime_gain_pct": 3.0},
            {"card_key": "Exp::006::Foil::2026-09-02", "quantity": 1, "unit_market_price": 10.03, "unit_low_price": 10.00, "unit_mid_price": 10.03, "unit_high_price": 12.00, "line_total": 10.03, "baseline_price": 10.00, "lifetime_gain_dollar": 0.03, "lifetime_gain_pct": 0.3},
        ])
        seed_summary(
            conn,
            "2026-09-25",
            total_value=177.41,
            total_cards=6,
            unique_items=6,
            lifetime_gain=15.41,
            lifetime_pct=9.5,
        )
        conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md, target_date="2026-09-25")
        content = Path(self.output_md).read_text(encoding="utf-8")

        # Rarity breakdown collapses shorthand into canonical tiers without orphan abbreviation rows
        rarity_section = content.split("### Rarity")[1].split("### Color")[0]
        self.assertIn("| **★ Iconic** |", rarity_section)
        self.assertIn("| **▣ Nova** |", rarity_section)
        self.assertIn("| **◆ Secret** |", rarity_section)
        self.assertIn("| **◈ Epic** |", rarity_section)
        self.assertIn("| **△ Uncommon** |", rarity_section)
        for orphan in ["| **IL** |", "| **IO** |", "| **NR** |", "| **UC** |", "| **E** |", "| **S** |"]:
            self.assertNotIn(orphan, rarity_section)

        # High-value singles partitioning correctly routes shorthand rarities
        singles_section = content.split("### High-Value Singles")[1].split("## 📈 Top Gainers")[0]
        iconic_sub = singles_section.split("#### Iconic Singles")[1].split("#### Nova Rare Singles")[0]
        nova_sub = singles_section.split("#### Nova Rare Singles")[1].split("#### Non-Iconic/Nova Rare Singles")[0]
        non_iconic_sub = singles_section.split("#### Non-Iconic/Nova Rare Singles")[1]

        self.assertIn("Royce", iconic_sub)
        self.assertIn("Alt Cunningham", iconic_sub)
        self.assertIn("Jackie Box Topper", nova_sub)
        self.assertIn("Johnny Secret", non_iconic_sub)
        self.assertIn("Flathead", non_iconic_sub)
        self.assertIn("Towerfall", non_iconic_sub)

        # Non-iconic section must NOT contain Iconic or Nova cards
        self.assertNotIn("Royce", non_iconic_sub)
        self.assertNotIn("Alt Cunningham", non_iconic_sub)
        self.assertNotIn("Jackie Box Topper", non_iconic_sub)

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
                total_value=1250.00,
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

    def test_breakdown_tables_two_percentage_columns_and_sum_to_100(self):
        """Verifies that all four breakdown tables have both percentage columns, and % of Portfolio Value sums to 100% in pure singles portfolios."""
        conn = sqlite3.connect(self.db_path)
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM daily_snapshots WHERE date = '2026-09-14'")
            cur.execute("DELETE FROM card_metadata")

            seed_metadata(conn, [
                {"card_key": "NC::001::Standard::2026-09-01", "name": "Card A", "expansion": "Night City", "finish": "Standard", "rarity": "Common", "color": "Green", "card_type": "Unit", "item_type": "Card", "print_number": "001"},
                {"card_key": "NC::002::Standard::2026-09-01", "name": "Card B", "expansion": "Night City", "finish": "Standard", "rarity": "Rare", "color": "Blue", "card_type": "Program", "item_type": "Card", "print_number": "002"},
                {"card_key": "ER::001::Standard::2026-09-01", "name": "Card C", "expansion": "Edgerunners", "finish": "Standard", "rarity": "Epic", "color": "Red", "card_type": "Legend", "item_type": "Card", "print_number": "001"},
            ])
            seed_snapshots(conn, "2026-09-14", [
                {"card_key": "NC::001::Standard::2026-09-01", "quantity": 5, "unit_market_price": 2.00, "line_total": 10.00},
                {"card_key": "NC::002::Standard::2026-09-01", "quantity": 3, "unit_market_price": 10.00, "line_total": 30.00},
                {"card_key": "ER::001::Standard::2026-09-01", "quantity": 2, "unit_market_price": 30.00, "line_total": 60.00},
            ])
            # Pure singles portfolio: 10 cards, total value $100.00, sealed = 0
            seed_summary(conn, "2026-09-14", total_value=100.00, total_cards=10, total_sealed=0)
        finally:
            conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")

        for section_name in ["Rarity", "Color", "Card Type", "Expansion"]:
            section_header = f"### {section_name}"
            self.assertIn(section_header, content)
            table_header = f"| {section_name} | Unique Items | Physical Copies | % of Cards | Market Value | % of Card Value |"
            self.assertIn(table_header, content)

            section_part = content.split(section_header)[1].split("###")[0]
            lines = [line.strip() for line in section_part.strip().split("\n") if line.strip().startswith("|") and not line.strip().startswith("| :---") and table_header not in line]

            total_pct_copies = 0.0
            total_copies = 0
            total_val = 0.0
            total_pct_val = 0.0

            for line in lines:
                parts = [p.strip() for p in line.split("|")[1:-1]]
                if len(parts) == 6:
                    total_copies += int(parts[2])
                    total_pct_copies += float(parts[3].replace("%", ""))
                    val_str = parts[4].replace("`", "").replace("$", "").replace(",", "")
                    total_val += float(val_str)
                    total_pct_val += float(parts[5].replace("%", ""))

            self.assertEqual(total_copies, 10, f"{section_name} total copies should equal total_cards (10)")
            self.assertAlmostEqual(total_pct_copies, 100.0, delta=0.2, msg=f"{section_name} % of Cards should sum to 100%")
            self.assertAlmostEqual(total_val, 100.00, delta=0.01, msg=f"{section_name} market values should sum to singles total ($100.00)")
            self.assertAlmostEqual(total_pct_val, 100.0, delta=0.2, msg=f"{section_name} % of Card Value should sum to 100% in pure singles portfolio")

        # Now test mixed portfolio with a sealed item ($100 singles + $100 sealed = $200 total)
        conn = sqlite3.connect(self.db_path)
        try:
            seed_metadata(conn, [
                {"card_key": "SEALED::Night City::999::2026-09-01", "name": "Booster Box", "expansion": "Night City", "finish": "Standard", "rarity": "Sealed", "color": "", "card_type": "", "item_type": "Sealed", "product_id": 999},
            ])
            seed_snapshots(conn, "2026-09-14", [
                {"card_key": "SEALED::Night City::999::2026-09-01", "quantity": 1, "unit_market_price": 100.00, "line_total": 100.00, "baseline_price": 100.00, "lifetime_gain_dollar": 0.00, "lifetime_gain_pct": 0.0},
            ])
            seed_summary(conn, "2026-09-14", total_value=200.00, total_cards=10, total_sealed=1)
        finally:
            conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content_mixed = Path(self.output_md).read_text(encoding="utf-8")

        singles_breakdown_totals = {}
        for section_name in ["Rarity", "Color", "Card Type", "Expansion"]:
            section_header = f"### {section_name}"
            table_header = f"| {section_name} | Unique Items | Physical Copies | % of Cards | Market Value | % of Card Value |"
            section_part = content_mixed.split(section_header)[1].split("###")[0]
            lines = [
                line.strip() for line in section_part.strip().split("\n")
                if line.strip().startswith("|") and not line.strip().startswith("| :---") and table_header not in line
            ]

            section_val = sum(float(line.split("|")[1:-1][4].replace("`", "").replace("$", "").replace(",", "").strip()) for line in lines if len(line.split("|")[1:-1]) == 6)
            total_pct_val = sum(float(line.split("|")[1:-1][5].replace("%", "").strip()) for line in lines if len(line.split("|")[1:-1]) == 6)
            self.assertAlmostEqual(section_val, 100.00, delta=0.01, msg=f"{section_name} market values should sum to singles total ($100.00)")
            self.assertAlmostEqual(total_pct_val, 100.0, delta=0.2, msg=f"{section_name} % of Card Value should sum to 100% in mixed portfolio (cards-only normalization)")
            singles_breakdown_totals[section_name] = section_val

        # Verify all four breakdown tables agree on the singles total ($100.00)
        self.assertEqual(len(set(singles_breakdown_totals.values())), 1)
        authoritative_singles_val = singles_breakdown_totals["Rarity"]

        # Verify Sealed Products table has % of Portfolio Value and matches individual share
        sealed_section = content_mixed.split("### Sealed Products")[1].split("## 📈 Top Gainers")[0]
        sealed_header = "| Product Name | Acquired | Qty | Unit Price | Total Value | % of Portfolio Value | Baseline Price | Lifetime Gain |"
        self.assertIn(sealed_header, sealed_section)
        sealed_lines = [
            line.strip() for line in sealed_section.strip().split("\n")
            if line.strip().startswith("|") and not line.strip().startswith("| :---") and sealed_header not in line
        ]
        self.assertEqual(len(sealed_lines), 1)
        sealed_parts = [p.strip() for p in sealed_lines[0].split("|")[1:-1]]
        # Product Name, Acquired, Qty, Unit Price, Total Value, % of Portfolio Value, Baseline Price, Lifetime Gain
        self.assertEqual(sealed_parts[0], "**Booster Box**")
        self.assertEqual(sealed_parts[5], "50.0%", "Booster box should represent 50.0% ($100 / $200) of total portfolio value")

        # Parse total portfolio value dynamically from executive summary
        summary_line = [l for l in content_mixed.split("\n") if "**Total Portfolio Market Value**" in l][0]
        rendered_total_val = float(summary_line.split("|")[2].replace("**", "").replace("`", "").replace("$", "").replace(",", "").strip())

        # Combined invariant: sealed share parsed from sealed table + singles share parsed from breakdown and summary tables equals 100.0%
        sealed_pct_sum = sum(float(l.split("|")[1:-1][5].replace("%", "").strip()) for l in sealed_lines)
        singles_portfolio_pct = (authoritative_singles_val / rendered_total_val) * 100.0
        self.assertAlmostEqual(sealed_pct_sum + singles_portfolio_pct, 100.0, delta=0.2)



    def test_multi_lot_consolidation_l7d_and_lifetime(self):
        """Verifies multi-lot holdings collapse into single rows with recomputed weighted baselines and basis percentages."""
        conn = sqlite3.connect(self.db_path)
        seed_metadata(conn, [
            {
                "card_key": "Exp::C001::Foil::2026-09-02",
                "product_id": 901,
                "name": "Consolidated Hero",
                "print_number": "C001",
                "expansion": "Welcome to Night City - Beta",
                "finish": "Foil",
                "rarity": "Epic",
                "color": "Red",
                "first_seen_date": "2026-09-02",
                "baseline_market_price": 10.00,
                "item_type": "Card",
            },
            {
                "card_key": "Exp::C001::Foil::2026-09-10",
                "product_id": 901,
                "name": "Consolidated Hero",
                "print_number": "C001",
                "expansion": "Welcome to Night City - Beta",
                "finish": "Foil",
                "rarity": "Epic",
                "color": "Red",
                "first_seen_date": "2026-09-10",
                "baseline_market_price": 14.00,
                "item_type": "Card",
            },
        ])
        # 7-day prior snapshot (2026-09-12)
        seed_snapshots(conn, "2026-09-12", [
            {
                "card_key": "Exp::C001::Foil::2026-09-02",
                "quantity": 1,
                "unit_market_price": 12.00,
                "unit_low_price": 12.00,
                "unit_mid_price": 12.00,
                "unit_high_price": 12.00,
                "line_total": 12.00,
                "baseline_price": 10.00,
                "lifetime_gain_dollar": 2.00,
                "lifetime_gain_pct": 20.0,
            },
            {
                "card_key": "Exp::C001::Foil::2026-09-10",
                "quantity": 2,
                "unit_market_price": 12.00,
                "unit_low_price": 12.00,
                "unit_mid_price": 12.00,
                "unit_high_price": 12.00,
                "line_total": 24.00,
                "baseline_price": 14.00,
                "lifetime_gain_dollar": -4.00,
                "lifetime_gain_pct": -14.3,
            },
        ])
        seed_summary(
            conn,
            "2026-09-12",
            total_value=36.00,
            total_cards=3,
            unique_items=2,
            lifetime_gain=-2.00,
            lifetime_pct=-5.3,
        )

        # Target snapshot (2026-09-19)
        seed_snapshots(conn, "2026-09-19", [
            {
                "card_key": "Exp::C001::Foil::2026-09-02",
                "quantity": 1,
                "unit_market_price": 20.00,
                "unit_low_price": 20.00,
                "unit_mid_price": 20.00,
                "unit_high_price": 20.00,
                "line_total": 20.00,
                "baseline_price": 10.00,
                "lifetime_gain_dollar": 10.00,
                "lifetime_gain_pct": 100.0,
            },
            {
                "card_key": "Exp::C001::Foil::2026-09-10",
                "quantity": 2,
                "unit_market_price": 20.00,
                "unit_low_price": 20.00,
                "unit_mid_price": 20.00,
                "unit_high_price": 20.00,
                "line_total": 40.00,
                "baseline_price": 14.00,
                "lifetime_gain_dollar": 12.00,
                "lifetime_gain_pct": 42.9,
            },
        ])
        seed_summary(
            conn,
            "2026-09-19",
            total_value=60.00,
            total_cards=3,
            unique_items=2,
            lifetime_gain=22.00,
            lifetime_pct=57.9,
        )
        conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md, target_date="2026-09-19")
        content = Path(self.output_md).read_text(encoding="utf-8")

        # 1. Lifetime Gainers check: consolidated into 1 row
        gainers_section = content.split("## 📈 Top Gainers")[1].split("## 📉 Top Decliners")[0]
        lifetime_gainers_part = gainers_section.split("### L7D")[0]
        self.assertEqual(lifetime_gainers_part.count("Consolidated Hero"), 1, "Card should appear exactly once in Lifetime Gainers")
        # Total Qty = 3, Unit Price = $20.00, Weighted Baseline = (10*1 + 14*2)/3 = $12.67, Lifetime Gain = +$22.00, Pct = +57.9%, Acq = 2026-09-02
        self.assertIn("| 🔴 **Consolidated Hero** | `2026-09-02` | **◈ Epic** | Foil | 3 | `$20.00` | `$12.67` | **+$22.00** | +57.9% |", lifetime_gainers_part)

        # 2. L7D Gainers check: consolidated into 1 row
        l7d_gainers_part = gainers_section.split("### L7D")[1]
        self.assertEqual(l7d_gainers_part.count("Consolidated Hero"), 1, "Card should appear exactly once in L7D Gainers")
        # Qty = 3, Unit Price = $20.00, 7D Prior = $12.00, Dollar Gain = +$24.00 (3 * $8.00), Pct = +66.7%, Acq = 2026-09-02
        self.assertIn("| 🔴 **Consolidated Hero** | `2026-09-02` | **◈ Epic** | Foil | 3 | `$20.00` | `$12.00` | **+$24.00** | +66.7% |", l7d_gainers_part)

    def test_lifetime_having_clause_nets_mixed_lots(self):
        """Verifies that a card with mixed winning and losing lots nets correctly via post-grouping HAVING."""
        conn = sqlite3.connect(self.db_path)
        seed_metadata(conn, [
            {
                "card_key": "Exp::MIX::Standard::2026-09-01",
                "product_id": 902,
                "name": "Mixed Result",
                "print_number": "M001",
                "expansion": "Welcome to Night City - Beta",
                "finish": "Standard",
                "rarity": "Rare",
                "color": "Blue",
                "first_seen_date": "2026-09-01",
                "baseline_market_price": 10.00,
                "item_type": "Card",
            },
            {
                "card_key": "Exp::MIX::Standard::2026-09-10",
                "product_id": 902,
                "name": "Mixed Result",
                "print_number": "M001",
                "expansion": "Welcome to Night City - Beta",
                "finish": "Standard",
                "rarity": "Rare",
                "color": "Blue",
                "first_seen_date": "2026-09-10",
                "baseline_market_price": 25.00,
                "item_type": "Card",
            },
        ])
        # Market price is 15.00.
        # Lot 1: baseline 10.00 -> gain +5.00
        # Lot 2: baseline 25.00 -> loss -10.00
        # Net gain: -5.00 (overall decliner)
        seed_snapshots(conn, "2026-09-19", [
            {
                "card_key": "Exp::MIX::Standard::2026-09-01",
                "quantity": 1,
                "unit_market_price": 15.00,
                "unit_low_price": 15.00,
                "unit_mid_price": 15.00,
                "unit_high_price": 15.00,
                "line_total": 15.00,
                "baseline_price": 10.00,
                "lifetime_gain_dollar": 5.00,
                "lifetime_gain_pct": 50.0,
            },
            {
                "card_key": "Exp::MIX::Standard::2026-09-10",
                "quantity": 1,
                "unit_market_price": 15.00,
                "unit_low_price": 15.00,
                "unit_mid_price": 15.00,
                "unit_high_price": 15.00,
                "line_total": 15.00,
                "baseline_price": 25.00,
                "lifetime_gain_dollar": -10.00,
                "lifetime_gain_pct": -40.0,
            },
        ])
        seed_summary(
            conn,
            "2026-09-19",
            total_value=30.00,
            total_cards=2,
            unique_items=2,
            lifetime_gain=-5.00,
            lifetime_pct=-14.3,
        )
        conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md, target_date="2026-09-19")
        content = Path(self.output_md).read_text(encoding="utf-8")

        gainers_section = content.split("## 📈 Top Gainers")[1].split("## 📉 Top Decliners")[0]
        decliners_section = content.split("## 📉 Top Decliners")[1]

        # Must NOT appear in Gainers despite lot 1 having +5.00 gain
        self.assertNotIn("Mixed Result", gainers_section)

        # Must appear in Decliners with net -$5.00
        self.assertIn("Mixed Result", decliners_section)
        # Weighted baseline = (10 + 25)/2 = $17.50, Total Loss = -$5.00, Pct = -14.3%
        self.assertIn("| 🔵 **Mixed Result** | `2026-09-01` | **◇ Rare** | Standard | 2 | `$15.00` | `$17.50` | **$-5.00** | -14.3% |", decliners_section)

    def test_lifetime_tables_expand_to_10_rows(self):
        """Verifies that Lifetime Gainers and Decliners tables output up to 10 rows."""
        conn = sqlite3.connect(self.db_path)
        meta_items = []
        snap_items = []
        # Seed 12 distinct cards with positive gains and 12 distinct cards with losses
        for i in range(1, 13):
            card_key_g = f"Exp::G{i:02d}::Standard::2026-09-01"
            meta_items.append({
                "card_key": card_key_g, "product_id": 1000 + i, "name": f"Gainer Card {i:02d}",
                "print_number": f"G{i:02d}", "expansion": "Expansion G", "finish": "Standard",
                "rarity": "Common", "color": "Green", "first_seen_date": "2026-09-01",
                "baseline_market_price": 5.00, "item_type": "Card",
            })
            snap_items.append({
                "card_key": card_key_g, "quantity": 1, "unit_market_price": 5.00 + i,
                "unit_low_price": 5.00 + i, "unit_mid_price": 5.00 + i, "unit_high_price": 5.00 + i,
                "line_total": 5.00 + i, "baseline_price": 5.00, "lifetime_gain_dollar": float(i),
                "lifetime_gain_pct": round((float(i) / 5.0) * 100.0, 1),
            })

            card_key_d = f"Exp::D{i:02d}::Standard::2026-09-01"
            meta_items.append({
                "card_key": card_key_d, "product_id": 2000 + i, "name": f"Decliner Card {i:02d}",
                "print_number": f"D{i:02d}", "expansion": "Expansion D", "finish": "Standard",
                "rarity": "Common", "color": "Yellow", "first_seen_date": "2026-09-01",
                "baseline_market_price": 20.00, "item_type": "Card",
            })
            snap_items.append({
                "card_key": card_key_d, "quantity": 1, "unit_market_price": 20.00 - i,
                "unit_low_price": 20.00 - i, "unit_mid_price": 20.00 - i, "unit_high_price": 20.00 - i,
                "line_total": 20.00 - i, "baseline_price": 20.00, "lifetime_gain_dollar": -float(i),
                "lifetime_gain_pct": round((-float(i) / 20.0) * 100.0, 1),
            })

        seed_metadata(conn, meta_items)
        seed_snapshots(conn, "2026-09-19", snap_items)
        seed_summary(conn, "2026-09-19", total_value=500.00, total_cards=24, unique_items=24, lifetime_gain=0.0)
        conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md, target_date="2026-09-19")
        content = Path(self.output_md).read_text(encoding="utf-8")

        gainers_section = content.split("## 📈 Top Gainers")[1].split("## 📉 Top Decliners")[0]
        decliners_section = content.split("## 📉 Top Decliners")[1]

        # Extract rows from markdown tables
        g_rows = [l for l in gainers_section.splitlines() if l.startswith("| 🟢 **Gainer Card")]
        d_rows = [l for l in decliners_section.splitlines() if l.startswith("| 🟡 **Decliner Card")]

        self.assertEqual(len(g_rows), 10, "Lifetime Gainers must output exactly 10 rows when >=10 eligible cards exist")
        self.assertEqual(len(d_rows), 10, "Lifetime Decliners must output exactly 10 rows when >=10 eligible cards exist")


if __name__ == "__main__":
    unittest.main()
