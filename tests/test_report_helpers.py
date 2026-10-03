"""
Unit tests for extracted report helper functions.
"""

import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tracker.report import (
    HIGH_VALUE_THRESHOLD,
    ensure_report_schema,
    fetch_portfolio_summary,
    fetch_portfolio_breakdowns,
    fetch_l7d_movers,
    fetch_performance_movers,
    render_markdown_report,
)
from tracker.valuation import init_database
from tests.fixtures import seed_metadata, seed_snapshots, seed_summary


class TestReportHelpers(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.db_path = str(self.test_dir / "test_report_helpers.db")
        self.conn = init_database(self.db_path)
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_ensure_schema_idempotent(self):
        # Running ensure_report_schema on an initialized DB performs a clean no-op
        ensure_report_schema(self.cur, self.conn)
        self.cur.execute("PRAGMA table_info(card_metadata)")
        card_cols = {col[1] for col in self.cur.fetchall()}
        self.assertIn("card_type", card_cols)

    def test_ensure_schema_migrates_bare_tables(self):
        # Verify migration adds columns to bare unmigrated tables
        bare_db = str(self.test_dir / "test_bare.db")
        bare_conn = sqlite3.connect(bare_db)
        bare_cur = bare_conn.cursor()
        bare_cur.execute("CREATE TABLE card_metadata (card_key TEXT PRIMARY KEY)")
        bare_cur.execute("CREATE TABLE portfolio_daily_summary (date TEXT PRIMARY KEY)")
        bare_conn.commit()

        ensure_report_schema(bare_cur, bare_conn)
        bare_cur.execute("PRAGMA table_info(card_metadata)")
        bare_card_cols = {col[1] for col in bare_cur.fetchall()}
        self.assertIn("card_type", bare_card_cols)

        bare_cur.execute("PRAGMA table_info(portfolio_daily_summary)")
        sum_cols = {col[1] for col in bare_cur.fetchall()}
        self.assertIn("collection_updated_at", sum_cols)
        self.assertIn("collection_source", sum_cols)
        self.assertIn("total_cost_basis", sum_cols)
        self.assertIn("net_unrealized_gain", sum_cols)
        self.assertIn("net_unrealized_pct", sum_cols)
        self.assertIn("purchases_updated_at", sum_cols)
        bare_conn.close()

    def test_fetch_portfolio_summary(self):
        seed_summary(
            self.conn,
            "2026-10-01",
            total_value=250.0,
            total_cards=10,
            unique_items=5,
            cost_basis=200.0,
            net_unrealized_gain=50.0,
        )

        # Specific target date
        res = fetch_portfolio_summary(self.cur, target_date="2026-10-01")
        self.assertIsNotNone(res)
        latest_date, row = res
        self.assertEqual(latest_date, "2026-10-01")
        self.assertEqual(row[0], 250.0)

        # Fallback to latest
        res_latest = fetch_portfolio_summary(self.cur, target_date=None)
        self.assertIsNotNone(res_latest)
        self.assertEqual(res_latest[0], "2026-10-01")

    def test_fetch_portfolio_breakdowns_and_movers(self):
        seed_metadata(self.conn, [
            {"card_key": "key1", "name": "Judy Alvarez", "expansion": "Night City", "finish": "Standard", "rarity": "Iconic", "color": "Green", "item_type": "Card", "card_type": "Character"},
            {"card_key": "key2", "name": "Booster Box", "expansion": "Night City", "finish": "Standard", "rarity": "Sealed", "color": "", "item_type": "Sealed", "card_type": "Sealed"},
        ])
        seed_snapshots(self.conn, "2026-10-01", [
            {"card_key": "key1", "quantity": 1, "unit_market_price": 15.0, "line_total": 15.0, "baseline_price": 10.0, "lifetime_gain_dollar": 5.0},
            {"card_key": "key2", "quantity": 1, "unit_market_price": 120.0, "line_total": 120.0, "baseline_price": 100.0, "lifetime_gain_dollar": 20.0},
        ])

        breakdowns = fetch_portfolio_breakdowns(self.cur, "2026-10-01")
        self.assertEqual(len(breakdowns["rarity"]), 1)
        self.assertEqual(breakdowns["rarity"][0][0], "Iconic")
        self.assertEqual(len(breakdowns["sealed"]), 1)

        movers = fetch_performance_movers(self.cur, "2026-10-01")
        self.assertEqual(len(movers["top_gainers"]), 1)
        self.assertEqual(len(movers["high_value_cards"]), 1)
        self.assertGreaterEqual(movers["high_value_cards"][0][6], HIGH_VALUE_THRESHOLD)

    def test_fetch_l7d_movers(self):
        seed_metadata(self.conn, [
            {"card_key": "card_a", "name": "Card A", "expansion": "Night City", "finish": "Standard", "rarity": "Rare", "color": "Red", "item_type": "Card"},
            {"card_key": "card_b", "name": "Card B", "expansion": "Night City", "finish": "Standard", "rarity": "Rare", "color": "Red", "item_type": "Card"},
        ])
        seed_snapshots(self.conn, "2026-09-24", [
            {"card_key": "card_a", "quantity": 1, "unit_market_price": 10.0, "baseline_price": 10.0},
            {"card_key": "card_b", "quantity": 1, "unit_market_price": 20.0, "baseline_price": 20.0},
        ])
        seed_snapshots(self.conn, "2026-10-01", [
            {"card_key": "card_a", "quantity": 1, "unit_market_price": 15.0, "baseline_price": 10.0},
            {"card_key": "card_b", "quantity": 1, "unit_market_price": 12.0, "baseline_price": 20.0},
        ])

        gainers = fetch_l7d_movers(self.cur, "2026-09-24", "2026-10-01", is_gainer=True)
        decliners = fetch_l7d_movers(self.cur, "2026-09-24", "2026-10-01", is_gainer=False)
        self.assertEqual(len(gainers), 1)
        self.assertEqual(gainers[0][0], "Card A")
        self.assertEqual(len(decliners), 1)
        self.assertEqual(decliners[0][0], "Card B")

    def test_render_markdown_report_structure(self):
        summary = (135.0, 1, 2, 0.0, 0.0, 25.0, 22.7, 1, "2026-10-01 10:00 AM", "CSV", 100.0, 35.0, 35.0, None)
        breakdowns = {
            "rarity": [("Iconic", 1, 1, 15.0)],
            "color": [("Green", 1, 1, 15.0)],
            "card_type": [("Character", 1, 1, 15.0)],
            "expansion": [("Night City", 1, 1, 15.0)],
            "sealed": [("Booster Box", "Night City", 1, 120.0, 120.0, 100.0, 20.0, 20.0, "2026-09-15")],
        }
        movers = {
            "top_gainers": [("Judy Alvarez", "Iconic", "Green", "Standard", 1, 15.0, 10.0, 5.0, 50.0, "2026-09-01")],
            "top_decliners": [],
            "high_value_cards": [("Judy Alvarez", "Night City", "Iconic", "Green", "Standard", 1, 15.0, 15.0, "Character")],
            "l7d_date": None,
            "top_l7d_gainers": [],
            "top_l7d_decliners": [],
        }
        md = render_markdown_report(summary, breakdowns, movers, "2026-10-01")
        self.assertIn("# 📊 Cyberpunk TCG Portfolio Valuation Report", md)
        self.assertIn("Total Portfolio Market Value", md)
        self.assertIn("Judy Alvarez", md)
        self.assertIn("Booster Box", md)
        self.assertIn("High-Value Singles", md)


if __name__ == "__main__":
    unittest.main()
