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

        # Dedicated sealed section exists with Acquired column
        self.assertIn("## 📦 Sealed Product Inventory", content)
        self.assertIn("Welcome to Night City - Beta Booster Box", content)
        self.assertIn("`2026-09-11`", content)
        self.assertIn("$235.17", content)

        # Rarity breakdown does NOT include "Sealed"
        self.assertNotIn("Sealed", content.split("## 💎 Portfolio Breakdown by Rarity")[1].split("## 🎨")[0])

        # High-Value Singles does NOT contain the booster box
        singles_section = content.split("## 🌟 High-Value Singles")[1].split("## 📈")[0]
        self.assertIn("Johnny Silverhand", singles_section)
        self.assertNotIn("Booster Box", singles_section)

        # Top Lifetime Gainers does NOT contain the booster box
        gainers_section = content.split("## 📈 Top Lifetime Gainers")[1].split("## 📉")[0]
        self.assertIn("Johnny Silverhand", gainers_section)
        self.assertNotIn("Booster Box", gainers_section)

    def test_report_header_timestamps(self):
        # Verify header labels are properly renamed
        generate_portfolio_report(db_path=self.db_path, output_md=self.output_md)
        content = Path(self.output_md).read_text(encoding="utf-8")
        self.assertIn("**Prices Last Updated**:", content)
        self.assertIn("**Report Generated**:", content)
        self.assertNotIn("**Snapshot Date**:", content)
        self.assertNotIn("**Last Updated**:", content)

        # When price cache with ISO timestamp is explicitly provided in an isolated dir
        price_dir = self.test_dir / "custom_prices"
        price_dir.mkdir(parents=True, exist_ok=True)
        sample_cache = {
            "date": "2026-09-14",
            "timestamp": "2026-09-14T21:50:58.160015+00:00",
            "prices": {},
        }
        (price_dir / "2026-09-14.json").write_text(json.dumps(sample_cache), encoding="utf-8")

        generate_portfolio_report(
            db_path=self.db_path,
            output_md=self.output_md,
            price_cache_dir=str(price_dir),
        )
        content_with_cache = Path(self.output_md).read_text(encoding="utf-8")
        self.assertIn("**Prices Last Updated**:", content_with_cache)
        self.assertIn("**Report Generated**:", content_with_cache)
        # Check that it converted ISO into localized datetime string containing date and PM
        self.assertIn("2026-09-14", content_with_cache.split("**Prices Last Updated**:")[1].split("\n")[0])
        self.assertIn("PM", content_with_cache.split("**Prices Last Updated**:")[1].split("\n")[0])


if __name__ == "__main__":
    unittest.main()
