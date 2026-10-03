"""
Cross-module end-to-end integration tests for the Cyberpunk TCG tracker pipeline.
Tests full pipeline execution, multi-day valuation progression, and purchase history invalidation.
All tests run with isolated temporary configurations without modifying repository data.
"""

import datetime
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import run_tracker
from tests.fixtures import (
    create_test_catalog,
    create_daily_prices,
    create_collection_csv,
    create_receipt_file,
)


class TestPipelineIntegration(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.prices_dir = self.test_dir / "prices"
        self.data_dir = self.test_dir / "data"
        self.purchases_dir = self.test_dir / "purchase_history"
        self.empty_repo_prices = self.test_dir / "empty_repo_prices"

        self.prices_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.purchases_dir.mkdir(parents=True, exist_ok=True)
        self.empty_repo_prices.mkdir(parents=True, exist_ok=True)

        self.db_path = self.test_dir / "portfolio.db"
        self.output_md = self.test_dir / "PORTFOLIO.md"
        self.collection_csv = self.data_dir / "active_collection.csv"
        self.ledger_path = self.test_dir / "purchase_history.csv"
        self.cache_path = self.test_dir / "purchase_history_cache.json"
        self.config_path = self.test_dir / "config.json"

        cfg_dict = {
            "database_path": str(self.db_path),
            "collection_csv": str(self.collection_csv),
            "price_cache_dir": str(self.prices_dir),
            "output_report": str(self.output_md),
            "purchase_history_dir": str(self.purchases_dir),
            "purchase_history_ledger": str(self.ledger_path),
            "purchase_history_cache": str(self.cache_path),
            "cardnexus_api_key": None,
        }
        self.config_path.write_text(json.dumps(cfg_dict, indent=2), encoding="utf-8")

        # Isolate from checked-in repository price caches and host environment API keys
        self.patch_val_repo = patch("tracker.valuation.REPO_PRICES_DIR", self.empty_repo_prices)
        self.patch_sync_repo = patch("tracker.sync.REPO_PRICES_DIR", self.empty_repo_prices)
        self.patch_val_repo.start()
        self.patch_sync_repo.start()
        self.patch_env = patch.dict(os.environ, {"CARDNEXUS_API_KEY": ""})
        self.patch_env.start()
        if sys.platform == "win32":
            self.patch_winreg = patch("winreg.OpenKey", side_effect=OSError)
            self.patch_winreg.start()
        else:
            self.patch_winreg = None

        # Seed standard catalog
        create_test_catalog(self.prices_dir)

    def tearDown(self):
        if self.patch_winreg:
            self.patch_winreg.stop()
        self.patch_env.stop()
        self.patch_sync_repo.stop()
        self.patch_val_repo.stop()
        self.temp_dir.cleanup()

    def test_full_pipeline_csv_e2e(self):
        today = datetime.datetime.now().astimezone().strftime("%Y-%m-%d")

        create_daily_prices(self.prices_dir, today)
        create_collection_csv(self.collection_csv)
        create_receipt_file(self.purchases_dir, "receipt_01.txt", "Date: 2026-09-01\nTCGplayer Order Total: $200.00")

        run_tracker.main(["--config", str(self.config_path)])

        self.assertTrue(self.db_path.exists())
        self.assertTrue(self.output_md.exists())

        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.cursor()

            cur.execute("""
            SELECT date, total_value, total_cards, total_cost_basis, net_unrealized_gain, net_unrealized_pct
            FROM portfolio_daily_summary
            WHERE date = ?
            """, (today,))
            row = cur.fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], today)
            self.assertEqual(row[1], 300.00)  # 2 * 25.00 (cards) + 1 * 250.00 (sealed)
            self.assertEqual(row[2], 2)       # 2 cards
            self.assertEqual(row[3], 200.00)  # receipt cost basis
            self.assertEqual(row[4], 100.00)  # 300.00 - 200.00
            self.assertEqual(row[5], 50.00)   # (100.00 / 200.00) * 100

            cur.execute("SELECT COUNT(*) FROM card_metadata")
            self.assertEqual(cur.fetchone()[0], 2)

            cur.execute("SELECT COUNT(*) FROM daily_snapshots WHERE date = ?", (today,))
            self.assertEqual(cur.fetchone()[0], 2)
        finally:
            conn.close()

        report_text = self.output_md.read_text(encoding="utf-8")
        self.assertIn("| **Total Portfolio Market Value** | **`$300.00`** |", report_text)
        self.assertIn("| **Total Invested Cost Basis** | **`$200.00`** |", report_text)
        self.assertIn("| **Net Unrealized Gain / Loss** | **+$100.00** (+50.00%) |", report_text)
        self.assertIn("Johnny Silverhand", report_text)
        self.assertIn("Welcome to Night City - Beta Booster Box", report_text)

    def test_multi_day_valuation_progression(self):
        date_1 = "2026-09-01"
        date_2 = "2026-09-02"

        # Day 1: Johnny Silverhand at $20.00
        create_daily_prices(self.prices_dir, date_1, {
            "date": date_1,
            "prices": {
                "101": {
                    "Normal": {"marketPrice": 20.00, "lowPrice": 18.00, "midPrice": 20.00, "highPrice": 22.00},
                },
            },
        })
        create_collection_csv(self.collection_csv, [
            {
                "name": "Johnny Silverhand",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "001",
                "finish": "Standard",
                "totalQtyOwned": 1,
                "notes": "2026-09-01: 1",
            },
        ])

        run_tracker.main(["--config", str(self.config_path), "--backfill", date_1])

        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.cursor()

            cur.execute("SELECT total_value, lifetime_dollar_gain, lifetime_pct_gain FROM portfolio_daily_summary WHERE date = ?", (date_1,))
            d1_summary = cur.fetchone()
            self.assertEqual(d1_summary[0], 20.00)
            self.assertEqual(d1_summary[1], 0.00)
            self.assertEqual(d1_summary[2], 0.0)

            cur.execute("SELECT unit_market_price, baseline_price, lifetime_gain_dollar, lifetime_gain_pct FROM daily_snapshots WHERE date = ?", (date_1,))
            d1_snapshot = cur.fetchone()
            self.assertEqual(d1_snapshot[0], 20.00)
            self.assertEqual(d1_snapshot[1], 20.00)
            self.assertEqual(d1_snapshot[2], 0.00)

            cur.execute("SELECT baseline_market_price, first_seen_date FROM card_metadata")
            d1_meta = cur.fetchone()
            self.assertEqual(d1_meta[0], 20.00)
            self.assertEqual(d1_meta[1], date_1)
        finally:
            conn.close()

        # Day 2: Johnny Silverhand rises to $30.00
        create_daily_prices(self.prices_dir, date_2, {
            "date": date_2,
            "prices": {
                "101": {
                    "Normal": {"marketPrice": 30.00, "lowPrice": 28.00, "midPrice": 30.00, "highPrice": 35.00},
                },
            },
        })

        run_tracker.main(["--config", str(self.config_path), "--backfill", date_2])

        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.cursor()

            cur.execute("SELECT total_value, lifetime_dollar_gain, lifetime_pct_gain FROM portfolio_daily_summary WHERE date = ?", (date_2,))
            d2_summary = cur.fetchone()
            self.assertEqual(d2_summary[0], 30.00)
            self.assertEqual(d2_summary[1], 10.00)
            self.assertEqual(d2_summary[2], 50.0)

            cur.execute("SELECT unit_market_price, baseline_price, lifetime_gain_dollar, lifetime_gain_pct FROM daily_snapshots WHERE date = ?", (date_2,))
            d2_snapshot = cur.fetchone()
            self.assertEqual(d2_snapshot[0], 30.00)
            self.assertEqual(d2_snapshot[1], 20.00)  # Baseline carried forward
            self.assertEqual(d2_snapshot[2], 10.00)
            self.assertEqual(d2_snapshot[3], 50.0)

            # Metadata baseline should remain unchanged from Day 1
            cur.execute("SELECT baseline_market_price, first_seen_date FROM card_metadata")
            d2_meta = cur.fetchone()
            self.assertEqual(d2_meta[0], 20.00)
            self.assertEqual(d2_meta[1], date_1)
        finally:
            conn.close()

    def test_purchase_history_invalidation_e2e(self):
        today = datetime.datetime.now().astimezone().strftime("%Y-%m-%d")

        create_daily_prices(self.prices_dir, today, {
            "date": today,
            "prices": {
                "101": {
                    "Normal": {"marketPrice": 50.00, "lowPrice": 45.00, "midPrice": 50.00, "highPrice": 55.00},
                },
            },
        })
        create_collection_csv(self.collection_csv, [
            {
                "name": "Johnny Silverhand",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "001",
                "finish": "Standard",
                "totalQtyOwned": 1,
                "notes": f"{today}: 1",
            },
        ])
        create_receipt_file(self.purchases_dir, "order_1.txt", "Date: 2026-09-01\nTotal Amount: $30.00")

        # Initial run: establishes today's valuation with 1 receipt ($30.00 cost basis)
        run_tracker.main(["--config", str(self.config_path)])

        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.cursor()
            cur.execute("SELECT total_cost_basis, net_unrealized_gain, net_unrealized_pct FROM portfolio_daily_summary WHERE date = ?", (today,))
            initial_summary = cur.fetchone()
            self.assertEqual(initial_summary[0], 30.00)
            self.assertEqual(initial_summary[1], 20.00)  # 50.00 - 30.00
            self.assertEqual(initial_summary[2], 66.67)  # (20.00 / 30.00) * 100
        finally:
            conn.close()

        # Add second receipt ($10.00 additional cost basis)
        create_receipt_file(self.purchases_dir, "order_2.txt", "Date: 2026-09-02\nTotal Amount: $10.00")

        # Second run without --force: fresh receipt discovery must invalidate valuation cache and recalculate today
        run_tracker.main(["--config", str(self.config_path)])

        conn = sqlite3.connect(str(self.db_path))
        try:
            cur = conn.cursor()
            cur.execute("SELECT total_cost_basis, net_unrealized_gain, net_unrealized_pct FROM portfolio_daily_summary WHERE date = ?", (today,))
            updated_summary = cur.fetchone()
            self.assertEqual(updated_summary[0], 40.00)  # 30.00 + 10.00
            self.assertEqual(updated_summary[1], 10.00)  # 50.00 - 40.00
            self.assertEqual(updated_summary[2], 25.00)  # (10.00 / 40.00) * 100
        finally:
            conn.close()

        report_text = self.output_md.read_text(encoding="utf-8")
        self.assertIn("| **Total Invested Cost Basis** | **`$40.00`** |", report_text)
        self.assertIn("| **Net Unrealized Gain / Loss** | **+$10.00** (+25.00%) |", report_text)


if __name__ == "__main__":
    unittest.main()
