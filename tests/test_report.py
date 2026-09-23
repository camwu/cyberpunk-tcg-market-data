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


class TestReportGeneration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.db_path = str(self.test_dir / "test_report.db")
        self.output_md = str(self.test_dir / "TEST_REPORT.md")

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE card_metadata (
            card_key TEXT PRIMARY KEY,
            product_id INTEGER,
            name TEXT,
            print_number TEXT,
            expansion TEXT,
            finish TEXT,
            rarity TEXT,
            color TEXT,
            first_seen_date TEXT,
            baseline_market_price REAL,
            item_type TEXT DEFAULT 'Card'
        )
        """)
        cur.execute("""
        CREATE TABLE daily_snapshots (
            date TEXT,
            card_key TEXT,
            quantity INTEGER,
            unit_market_price REAL,
            unit_low_price REAL,
            unit_mid_price REAL,
            unit_high_price REAL,
            line_total REAL,
            baseline_price REAL,
            lifetime_gain_dollar REAL,
            lifetime_gain_pct REAL,
            PRIMARY KEY (date, card_key)
        )
        """)
        cur.execute("""
        CREATE TABLE portfolio_daily_summary (
            date TEXT PRIMARY KEY,
            total_value REAL,
            total_cards INTEGER,
            unique_items INTEGER,
            l7d_dollar_delta REAL,
            l7d_pct_delta REAL,
            lifetime_dollar_gain REAL,
            lifetime_pct_gain REAL,
            total_sealed INTEGER DEFAULT 0
        )
        """)

        # Insert 1 single card and 1 sealed booster box
        cur.execute("""
        INSERT INTO card_metadata VALUES
        ('Exp::001::Standard', 101, 'Johnny Silverhand', '001', 'Welcome to Night City - Beta', 'Standard', 'Iconic', 'Red', '2026-09-11', 15.00, 'Card'),
        ('SEALED::Welcome to Night City - Beta::714346::2026-09-11', 714346, 'Welcome to Night City - Beta Booster Box', NULL, 'Welcome to Night City - Beta', 'Standard', 'Sealed', NULL, '2026-09-11', 216.08, 'Sealed')
        """)

        cur.execute("""
        INSERT INTO daily_snapshots VALUES
        ('2026-09-14', 'Exp::001::Standard', 1, 20.00, 18.00, 20.00, 25.00, 20.00, 15.00, 5.00, 33.33),
        ('2026-09-14', 'SEALED::Welcome to Night City - Beta::714346::2026-09-11', 1, 235.17, 230.00, 235.00, 250.00, 235.17, 216.08, 19.09, 8.83)
        """)

        cur.execute("""
        INSERT INTO portfolio_daily_summary VALUES
        ('2026-09-14', 255.17, 1, 2, 0.0, 0.0, 24.09, 10.42, 1)
        """)

        conn.commit()
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
            cur = conn.cursor()
            # Add a card from a second expansion: Cyberpunk Edgerunners
            cur.execute("""
            INSERT INTO card_metadata VALUES
            ('Exp2::002::Standard', 102, 'David Martinez', '002', 'Cyberpunk Edgerunners', 'Standard', 'Rare', 'Yellow', '2026-09-11', 50.00, 'Card')
            """)
            cur.execute("""
            INSERT INTO daily_snapshots VALUES
            ('2026-09-14', 'Exp2::002::Standard', 2, 60.00, 55.00, 60.00, 70.00, 120.00, 50.00, 10.00, 20.00)
            """)
            # Update summary total_val to reflect the added card: 255.17 + 120.00 = 375.17
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
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO portfolio_daily_summary VALUES
        ('2026-09-13', 200.00, 1, 1, 0.0, 0.0, 10.00, 5.00, 0)
        """)
        cur.execute("""
        INSERT INTO daily_snapshots VALUES
        ('2026-09-13', 'Exp::001::Standard', 1, 15.00, 15.00, 15.00, 20.00, 15.00, 15.00, 0.00, 0.00)
        """)
        conn.commit()
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
        cur = conn.cursor()

        # Add a decliner card
        cur.execute("""
        INSERT INTO card_metadata VALUES
        ('Exp::002::Standard', 102, 'V - Nomad', '002', 'Welcome to Night City - Beta', 'Standard', 'Rare', 'Yellow', '2026-09-07', 30.00, 'Card')
        """)

        # Add prior day snapshot (2026-09-07)
        cur.execute("""
        INSERT INTO portfolio_daily_summary VALUES
        ('2026-09-07', 245.00, 2, 3, 0.0, 0.0, 0.0, 0.0, 1)
        """)
        cur.execute("""
        INSERT INTO daily_snapshots VALUES
        ('2026-09-07', 'Exp::001::Standard', 1, 15.00, 15.00, 15.00, 20.00, 15.00, 15.00, 0.00, 0.00),
        ('2026-09-07', 'Exp::002::Standard', 1, 30.00, 28.00, 30.00, 35.00, 30.00, 30.00, 0.00, 0.00),
        ('2026-09-07', 'SEALED::Welcome to Night City - Beta::714346::2026-09-11', 1, 200.00, 190.00, 200.00, 220.00, 200.00, 200.00, 0.00, 0.00)
        """)

        # Add 2026-09-14 snapshot for the decliner card
        cur.execute("""
        INSERT INTO daily_snapshots VALUES
        ('2026-09-14', 'Exp::002::Standard', 1, 20.00, 18.00, 20.00, 25.00, 20.00, 30.00, -10.00, -33.33)
        """)
        conn.commit()
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
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO card_metadata VALUES
        ('Exp::003::Standard', 103, 'Uncolored Card', '003', 'Welcome to Night City - Beta', 'Standard', 'Common', '', '2026-09-14', 5.00, 'Card')
        """)
        cur.execute("""
        INSERT INTO daily_snapshots VALUES
        ('2026-09-14', 'Exp::003::Standard', 1, 5.00, 5.00, 5.00, 5.00, 5.00, 5.00, 0.00, 0.00)
        """)
        conn.commit()
        conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")

        self.assertIn("### Color", content)
        self.assertNotIn("****", content)
        self.assertIn("| **Unknown** | 1 | 1 | `$5.00` |", content)

    def test_multi_lot_high_value_cards_aggregated(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        # Insert 2 lots for Towerfall (print B034, unit_market_price 15.00)
        cur.execute("""
        INSERT INTO card_metadata VALUES
        ('Exp::B034::Standard::2026-09-02', 500, 'Towerfall', 'B034', 'Welcome to Night City - Beta', 'Standard', 'Rare', 'Blue', '2026-09-02', 10.00, 'Card'),
        ('Exp::B034::Standard::2026-09-18', 500, 'Towerfall', 'B034', 'Welcome to Night City - Beta', 'Standard', 'Rare', 'Blue', '2026-09-18', 15.00, 'Card')
        """)
        cur.execute("""
        INSERT INTO daily_snapshots VALUES
        ('2026-09-18', 'Exp::B034::Standard::2026-09-02', 1, 15.00, 15.00, 15.00, 18.00, 15.00, 10.00, 5.00, 50.0),
        ('2026-09-18', 'Exp::B034::Standard::2026-09-18', 2, 15.00, 15.00, 15.00, 18.00, 30.00, 15.00, 0.00, 0.0)
        """)
        cur.execute("""
        INSERT INTO portfolio_daily_summary VALUES
        ('2026-09-18', 45.00, 3, 1, 0.0, 0.0, 5.00, 12.5, 0)
        """)
        conn.commit()
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
        cur = conn.cursor()
        # Insert 2 lots for Towerfall (print B034)
        cur.execute("""
        INSERT INTO card_metadata VALUES
        ('Exp::B034::Standard::2026-09-02', 500, 'Towerfall', 'B034', 'Welcome to Night City - Beta', 'Standard', 'Rare', 'Blue', '2026-09-02', 10.00, 'Card'),
        ('Exp::B034::Standard::2026-09-18', 500, 'Towerfall', 'B034', 'Welcome to Night City - Beta', 'Standard', 'Rare', 'Blue', '2026-09-18', 15.00, 'Card')
        """)
        cur.execute("""
        INSERT INTO daily_snapshots VALUES
        ('2026-09-18', 'Exp::B034::Standard::2026-09-02', 1, 15.00, 15.00, 15.00, 18.00, 15.00, 10.00, 5.00, 50.0),
        ('2026-09-18', 'Exp::B034::Standard::2026-09-18', 2, 15.00, 15.00, 15.00, 18.00, 30.00, 15.00, 0.00, 0.0)
        """)
        cur.execute("""
        INSERT INTO portfolio_daily_summary VALUES
        ('2026-09-18', 45.00, 3, 1, 0.0, 0.0, 5.00, 12.5, 0)
        """)
        conn.commit()
        conn.close()

        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md, target_date="2026-09-18")
        content = Path(self.output_md).read_text(encoding="utf-8")

        # Rarity breakdown should count 1 distinct card and 3 copies for Rare
        rarity_section = content.split("### Rarity")[1].split("### Color")[0]
        self.assertIn("| **◇ Rare** | 1 | 3 | `$45.00` |", rarity_section)

    def test_top_gainers_ranks_by_per_unit_delta_and_includes_acquired_date(self):
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        # Card A: 1 copy, unit price 20.00, baseline 10.00 -> +10.00/unit, total gain $10.00
        # Card B: 2 copies, unit price 20.00, baseline 12.00 -> +8.00/unit, total gain $16.00
        cur.execute("""
        INSERT INTO card_metadata VALUES
        ('Exp::A::Standard::2026-09-02', 601, 'Card Alpha', 'A', 'Expansion A', 'Standard', 'Rare', 'Red', '2026-09-02', 10.00, 'Card'),
        ('Exp::B::Standard::2026-09-10', 602, 'Card Beta', 'B', 'Expansion A', 'Standard', 'Rare', 'Blue', '2026-09-10', 12.00, 'Card')
        """)
        cur.execute("""
        INSERT INTO daily_snapshots VALUES
        ('2026-09-19', 'Exp::A::Standard::2026-09-02', 1, 20.00, 20.00, 20.00, 20.00, 20.00, 10.00, 10.00, 100.0),
        ('2026-09-19', 'Exp::B::Standard::2026-09-10', 2, 20.00, 20.00, 20.00, 20.00, 40.00, 12.00, 16.00, 66.7)
        """)
        cur.execute("""
        INSERT INTO portfolio_daily_summary VALUES
        ('2026-09-19', 60.00, 3, 2, 0.0, 0.0, 26.00, 76.5, 0)
        """)
        conn.commit()
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
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(card_metadata)")
        cols = [col[1] for col in cur.fetchall()]
        if "card_type" not in cols:
            cur.execute("ALTER TABLE card_metadata ADD COLUMN card_type TEXT")

        cur.execute("""
        INSERT INTO card_metadata (card_key, product_id, name, print_number, expansion, finish, rarity, color, first_seen_date, baseline_market_price, item_type, card_type)
        VALUES
        ('Exp::001::Foil::2026-09-02', 801, 'Hanako Iconic', 'B141', 'Welcome to Night City - Beta', 'Foil', 'Iconic Legend', 'Green', '2026-09-02', 40.00, 'Card', 'Legend'),
        ('Exp::002::Foil::2026-09-02', 802, 'Mantis Nova', '008', 'Welcome to Night City - Beta', 'Foil', 'Nova Rare', 'Red', '2026-09-02', 25.00, 'Card', 'Gear'),
        ('Exp::003::Foil::2026-09-02', 803, 'Jackie Epic', 'B050', 'Welcome to Night City - Beta', 'Foil', 'Epic', 'Blue', '2026-09-02', 10.00, 'Card', 'Unit')
        """)

        cur.execute("""
        INSERT INTO daily_snapshots VALUES
        ('2026-09-20', 'Exp::001::Foil::2026-09-02', 1, 50.00, 50.00, 50.00, 50.00, 50.00, 40.00, 10.00, 25.0),
        ('2026-09-20', 'Exp::002::Foil::2026-09-02', 1, 30.00, 30.00, 30.00, 30.00, 30.00, 25.00, 5.00, 20.0),
        ('2026-09-20', 'Exp::003::Foil::2026-09-02', 1, 15.00, 15.00, 15.00, 15.00, 15.00, 10.00, 5.00, 50.0)
        """)

        cur.execute("""
        INSERT INTO portfolio_daily_summary VALUES
        ('2026-09-20', 95.00, 3, 3, 0.0, 0.0, 20.00, 26.7, 0)
        """)
        conn.commit()
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


if __name__ == "__main__":
    unittest.main()
