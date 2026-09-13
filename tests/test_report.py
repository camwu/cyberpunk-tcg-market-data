"""
Automated unit tests for portfolio report formatting and markdown generation.
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tracker.report import (
    normalize_rarity,
    format_rarity,
    format_color,
    generate_portfolio_report,
)


class TestPortfolioReport(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_normalize_rarity(self):
        self.assertEqual(normalize_rarity("Common"), "Common")
        self.assertEqual(normalize_rarity("Iconic (Full Art)"), "Iconic")
        self.assertEqual(normalize_rarity("Nova Holo"), "Nova")
        self.assertEqual(normalize_rarity(None), "Unknown")
        self.assertEqual(normalize_rarity(""), "Unknown")

    def test_format_rarity(self):
        self.assertEqual(format_rarity("Common"), "△ Common")
        self.assertEqual(format_rarity("Epic"), "🞚 Epic")
        self.assertEqual(format_rarity("Iconic", bold=True), "**★ Iconic**")
        self.assertEqual(format_rarity("Custom"), "Custom")

    def test_format_color(self):
        self.assertEqual(format_color("Green"), "🟢 Green")
        self.assertEqual(format_color("Blue"), "🔵 Blue")
        self.assertEqual(format_color("Red"), "🔴 Red")
        self.assertEqual(format_color("Yellow"), "🟡 Yellow")
        self.assertEqual(format_color("Orange"), "Orange")

    def test_generate_report_missing_db(self):
        with self.assertRaises(FileNotFoundError):
            generate_portfolio_report("nonexistent_path.db", "out.md")

    def test_generate_report_empty_db(self):
        db_file = self.test_dir / "empty.db"
        conn = sqlite3.connect(str(db_file))
        conn.execute("CREATE TABLE portfolio_daily_summary (date TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()

        out_md = self.test_dir / "out.md"
        # Should not raise exception
        generate_portfolio_report(str(db_file), str(out_md))
        self.assertFalse(out_md.exists())

    def test_generate_portfolio_report_end_to_end(self):
        db_file = self.test_dir / "test_portfolio.db"
        out_md = self.test_dir / "TEST_SUMMARY.md"

        conn = sqlite3.connect(str(db_file))
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
            baseline_market_price REAL
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
            lifetime_pct_gain REAL
        )
        """)

        test_date = "2026-09-13"
        cur.execute("""
        INSERT INTO card_metadata VALUES
        ('Promo::001::Standard', 101, 'V - Nomad', '001', 'Promo', 'Standard', 'Iconic', 'Yellow', '2026-09-10', 20.0),
        ('Core::010::Foil', 102, 'Judy Alvarez', '010', 'Core', 'Foil', 'Rare', 'Blue', '2026-09-10', 5.0)
        """)

        cur.execute("""
        INSERT INTO daily_snapshots VALUES
        ('2026-09-13', 'Promo::001::Standard', 2, 25.0, 20.0, 25.0, 30.0, 50.0, 20.0, 10.0, 25.0),
        ('2026-09-13', 'Core::010::Foil', 1, 4.0, 3.0, 4.0, 5.0, 4.0, 5.0, -1.0, -20.0)
        """)

        cur.execute("""
        INSERT INTO portfolio_daily_summary VALUES
        ('2026-09-13', 54.0, 3, 2, 4.0, 8.0, 9.0, 20.0)
        """)

        conn.commit()
        conn.close()

        generate_portfolio_report(str(db_file), str(out_md))

        self.assertTrue(out_md.exists())
        content = out_md.read_text(encoding="utf-8")
        self.assertIn("Cyberpunk TCG Portfolio Valuation Report", content)
        self.assertIn("$54.00", content)
        self.assertIn("V - Nomad", content)
        self.assertIn("Judy Alvarez", content)
        self.assertIn("★ Iconic", content)
        self.assertIn("◇ Rare", content)
        self.assertIn("🟡 Yellow", content)
        self.assertIn("🔵 Blue", content)


if __name__ == "__main__":
    unittest.main()
