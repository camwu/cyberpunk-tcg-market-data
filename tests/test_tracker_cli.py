"""
Automated unit tests for CLI collection source resolution, automatic 24-hour
CardNexus API synchronization, cache evaluation, and orchestrator propagation.
"""

import datetime
import io
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import run_tracker
from tracker.config import TrackerConfig
from tracker.valuation import init_database
from tracker.validation import CollectionValidationError


class TestTrackerCLICollectionSource(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

        self.collection_csv = self.test_dir / "my_collection.csv"
        self.collection_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\nJohnny Silverhand,Welcome to Night City - Beta,001,Standard,1,\n",
            encoding="utf-8",
        )

        self.db_path = str(self.test_dir / "test.db")
        self.price_dir = str(self.test_dir / "prices")
        os.makedirs(self.price_dir, exist_ok=True)

        self.dummy_price_file = os.path.join(self.price_dir, "2026-09-22.json")
        with open(self.dummy_price_file, "w", encoding="utf-8") as f:
            f.write('{"date": "2026-09-22", "prices": {}}')

        self.api_key_for_test = "test_api_key_123"

        self.load_config_patcher = patch("run_tracker.load_config", side_effect=self._fake_load_config)
        self.mock_load_config = self.load_config_patcher.start()

    def tearDown(self):
        self.load_config_patcher.stop()
        self.temp_dir.cleanup()

    def _fake_load_config(self, **kwargs):
        return TrackerConfig(
            collection_csv=kwargs.get("collection_csv") or str(self.collection_csv),
            database_path=kwargs.get("database_path") or self.db_path,
            price_cache_dir=kwargs.get("price_cache_dir") or self.price_dir,
            output_report=kwargs.get("output_report") or str(self.test_dir / "LATEST_PORTFOLIO_SUMMARY.md"),
            sealed_csv=kwargs.get("sealed_csv"),
            cardnexus_api_key=self.api_key_for_test,
            purchase_history_dir=kwargs.get("purchase_history_dir"),
            purchase_history_ledger=kwargs.get("purchase_history_ledger"),
            purchase_history_cache=kwargs.get("purchase_history_cache"),
        )

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.sync_market_prices")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_missing_api_key_falls_back_to_offline_csv(self, mock_calc, mock_sync, mock_report):
        self.api_key_for_test = None
        mock_sync.return_value = self.dummy_price_file
        test_args = [
            "run_tracker.py",
            "--collection", str(self.collection_csv),
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_calc.assert_called_once()
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_source"), "CSV (my_collection.csv)")

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.sync_market_prices")
    @patch("run_tracker.sync_cardnexus_collection")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_auto_sync_triggers_when_cache_missing_or_stale(self, mock_calc, mock_sync_cn, mock_sync_prices, mock_report):
        mock_sync_prices.return_value = self.dummy_price_file
        target_csv = self.test_dir / "active_collection.csv"
        target_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\nJohnny Silverhand,Welcome to Night City - Beta,001,Standard,1,\n",
            encoding="utf-8",
        )
        stale_time = time.time() - 90000
        os.utime(target_csv, (stale_time, stale_time))

        mock_sync_cn.return_value = (True, str(target_csv), 1)

        test_args = [
            "run_tracker.py",
            "--collection", str(target_csv),
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_sync_cn.assert_called_once()
        mock_calc.assert_called_once()
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_source"), "CardNexus API")
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_path"), str(target_csv))
        self.assertTrue(mock_calc.call_args.kwargs.get("force"))

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.sync_market_prices")
    @patch("run_tracker.sync_cardnexus_collection")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_auto_sync_reuses_cached_collection_under_24h(self, mock_calc, mock_sync_cn, mock_sync_prices, mock_report):
        mock_sync_prices.return_value = self.dummy_price_file
        target_csv = self.test_dir / "active_collection.csv"
        target_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\nJohnny Silverhand,Welcome to Night City - Beta,001,Standard,1,\n",
            encoding="utf-8",
        )
        fresh_time = time.time() - 3600
        os.utime(target_csv, (fresh_time, fresh_time))

        test_args = [
            "run_tracker.py",
            "--collection", str(target_csv),
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_sync_cn.assert_not_called()
        mock_calc.assert_called_once()
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_source"), "CardNexus API (cached)")
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_path"), str(target_csv))
        self.assertFalse(mock_calc.call_args.kwargs.get("force"))

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.sync_market_prices")
    @patch("run_tracker.sync_cardnexus_collection")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_sync_collection_flag_bypasses_fresh_cache(self, mock_calc, mock_sync_cn, mock_sync_prices, mock_report):
        mock_sync_prices.return_value = self.dummy_price_file
        target_csv = self.test_dir / "active_collection.csv"
        target_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\nJohnny Silverhand,Welcome to Night City - Beta,001,Standard,1,\n",
            encoding="utf-8",
        )
        fresh_time = time.time() - 3600
        os.utime(target_csv, (fresh_time, fresh_time))

        mock_sync_cn.return_value = (True, str(target_csv), 1)

        test_args = [
            "run_tracker.py",
            "--collection", str(target_csv),
            "--sync-collection",
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_sync_cn.assert_called_once()
        mock_calc.assert_called_once()
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_source"), "CardNexus API")
        self.assertTrue(mock_calc.call_args.kwargs.get("force"))

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.sync_market_prices")
    @patch("run_tracker.sync_cardnexus_collection")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_no_sync_collection_flag_skips_api_call(self, mock_calc, mock_sync_cn, mock_sync_prices, mock_report):
        mock_sync_prices.return_value = self.dummy_price_file
        test_args = [
            "run_tracker.py",
            "--collection", str(self.collection_csv),
            "--no-sync-collection",
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_sync_cn.assert_not_called()
        mock_calc.assert_called_once()
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_source"), "CSV (my_collection.csv)")
        self.assertFalse(mock_calc.call_args.kwargs.get("force"))

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.sync_market_prices")
    @patch("run_tracker.sync_cardnexus_collection")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_import_file_preempts_auto_sync(self, mock_calc, mock_sync_cn, mock_sync, mock_report):
        mock_sync.return_value = self.dummy_price_file
        import_csv = self.test_dir / "export_september.csv"
        import_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\nJohnny Silverhand,Welcome to Night City - Beta,001,Standard,1,\n",
            encoding="utf-8",
        )

        test_args = [
            "run_tracker.py",
            "--collection", str(self.collection_csv),
            "--import-file", str(import_csv),
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_sync_cn.assert_not_called()
        mock_calc.assert_called_once()
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_source"), "CSV (export_september.csv)")
        self.assertTrue(mock_calc.call_args.kwargs.get("force"))

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.sync_market_prices")
    @patch("run_tracker.sync_cardnexus_collection")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_api_failure_falls_back_to_existing_collection(self, mock_calc, mock_sync_cn, mock_sync_prices, mock_report):
        mock_sync_prices.return_value = self.dummy_price_file
        mock_sync_cn.return_value = (False, None, 0)
        target_csv = self.test_dir / "active_collection.csv"
        target_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\nJohnny Silverhand,Welcome to Night City - Beta,001,Standard,1,\n",
            encoding="utf-8",
        )

        test_args = [
            "run_tracker.py",
            "--collection", str(target_csv),
            "--sync-collection",
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr, \
             patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_sync_cn.assert_called_once()
        mock_calc.assert_called_once()
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_source"), "CardNexus API (fallback)")
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_path"), str(target_csv))
        self.assertTrue(mock_calc.call_args.kwargs.get("force"))
        self.assertIn("Warning: CardNexus API sync failed. Falling back to cached collection:", mock_stderr.getvalue())

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.sync_market_prices")
    @patch("run_tracker.sync_cardnexus_collection")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_auto_sync_failure_preserves_force_false(self, mock_calc, mock_sync_cn, mock_sync_prices, mock_report):
        mock_sync_prices.return_value = self.dummy_price_file
        mock_sync_cn.return_value = (False, None, 0)
        target_csv = self.test_dir / "active_collection.csv"
        target_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\nJohnny Silverhand,Welcome to Night City - Beta,001,Standard,1,\n",
            encoding="utf-8",
        )
        stale_time = time.time() - 90000
        os.utime(target_csv, (stale_time, stale_time))

        test_args = [
            "run_tracker.py",
            "--collection", str(target_csv),
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr, \
             patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_sync_cn.assert_called_once()
        mock_calc.assert_called_once()
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_source"), "CardNexus API (fallback)")
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_path"), str(target_csv))
        self.assertFalse(mock_calc.call_args.kwargs.get("force"))
        self.assertIn("Warning: CardNexus API sync failed. Falling back to cached collection:", mock_stderr.getvalue())

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.calculate_portfolio_valuation")
    @patch("run_tracker.sync_cardnexus_collection", side_effect=CollectionValidationError("Lot quantity mismatch"))
    def test_sync_validation_error_halts_execution_with_exit_1(self, mock_sync_cn, mock_calc, mock_report):
        target_csv = self.test_dir / "active_collection.csv"
        target_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned,notes\nJohnny Silverhand,Welcome to Night City - Beta,001,Standard,1,\n",
            encoding="utf-8",
        )
        test_args = [
            "run_tracker.py",
            "--collection", str(target_csv),
            "--sync-collection",
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch.object(sys, "argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                run_tracker.main()
            self.assertEqual(cm.exception.code, 1)

        mock_sync_cn.assert_called_once()
        mock_calc.assert_not_called()
        mock_report.assert_not_called()

    @patch("run_tracker.sync_cardnexus_collection")
    def test_api_failure_exits_when_no_collection_file_exists(self, mock_sync_cn):
        non_existent_file = str(self.test_dir / "does_not_exist.csv")
        mock_sync_cn.return_value = (False, None, 0)

        test_args = [
            "run_tracker.py",
            "--collection", non_existent_file,
            "--sync-collection",
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr, \
             patch.object(sys, "argv", test_args):
            with self.assertRaises(SystemExit) as cm:
                run_tracker.main()
            self.assertEqual(cm.exception.code, 1)
            self.assertIn("Error: CardNexus API sync failed and no collection file exists.", mock_stderr.getvalue())

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.backfill_market_prices")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_backfill_propagates_collection_source(self, mock_calc, mock_backfill, mock_report):
        self.api_key_for_test = None
        test_args = [
            "run_tracker.py",
            "--collection", str(self.collection_csv),
            "--backfill", "2026-09-15",
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_calc.assert_called_once()
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_source"), "CSV (my_collection.csv)")

    @patch("run_tracker.sync_purchase_history", return_value=(0.0, [], False))
    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_reparse_purchases_cli_flag(self, mock_calc, mock_report, mock_sync_purchases):
        self.api_key_for_test = None
        test_args = [
            "run_tracker.py",
            "--collection", str(self.collection_csv),
            "--reparse-purchases",
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_sync_purchases.assert_called_once()
        self.assertTrue(mock_sync_purchases.call_args.kwargs.get("reparse"))
        mock_calc.assert_called_once()
        self.assertTrue(mock_calc.call_args.kwargs.get("force"))

    @patch("run_tracker.sync_purchase_history", return_value=(50.0, [], True))
    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.sync_market_prices")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_purchases_updated_invalidates_valuation_cache(self, mock_calc, mock_sync_prices, mock_report, mock_sync_purchases):
        self.api_key_for_test = None
        mock_sync_prices.return_value = self.dummy_price_file
        test_args = [
            "run_tracker.py",
            "--collection", str(self.collection_csv),
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_calc.assert_called_once()
        self.assertTrue(mock_calc.call_args.kwargs.get("force"))

    @patch("run_tracker.sync_purchase_history", return_value=(50.0, [], False))
    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.sync_market_prices")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_purchases_not_updated_does_not_force_valuation(self, mock_calc, mock_sync_prices, mock_report, mock_sync_purchases):
        self.api_key_for_test = None
        mock_sync_prices.return_value = self.dummy_price_file
        test_args = [
            "run_tracker.py",
            "--collection", str(self.collection_csv),
            "--no-sync-collection",
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_calc.assert_called_once()
        self.assertFalse(mock_calc.call_args.kwargs.get("force"))

    @patch("run_tracker.sync_purchase_history", return_value=(50.0, [], True))
    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.backfill_market_prices")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_backfill_does_not_force_when_purchases_updated(self, mock_calc, mock_backfill, mock_report, mock_sync_purchases):
        self.api_key_for_test = None
        test_args = [
            "run_tracker.py",
            "--collection", str(self.collection_csv),
            "--backfill", "2026-09-15",
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_calc.assert_called_once()
        self.assertFalse(mock_calc.call_args.kwargs.get("force"))

    def test_integration_new_receipt_invalidates_same_day_snapshot(self):
        self.api_key_for_test = None
        today = datetime.datetime.now().astimezone().strftime("%Y-%m-%d")
        seed_timestamp = f"{today} 09:05 PM"

        # Create price file for today
        today_price_file = os.path.join(self.price_dir, f"{today}.json")
        with open(today_price_file, "w", encoding="utf-8") as f:
            f.write(f'{{"date": "{today}", "prices": {{}}}}')

        # Initialize real DB with a pre-existing snapshot for today
        conn = init_database(self.db_path)
        cur = conn.cursor()
        cur.execute("""
        INSERT INTO portfolio_daily_summary (
            date, total_value, total_cards, unique_items, l7d_dollar_delta, l7d_pct_delta,
            lifetime_dollar_gain, lifetime_pct_gain, total_sealed, collection_updated_at,
            collection_source, total_cost_basis, net_unrealized_gain, net_unrealized_pct,
            purchases_updated_at
        ) VALUES (?, 10.0, 1, 1, 0.0, 0.0, 0.0, 0.0, 0, ?, 'CSV', 400.0, -390.0, -97.5, ?)
        """, (today, seed_timestamp, seed_timestamp))
        conn.commit()
        conn.close()

        # Set up purchase history folder and drop a new receipt
        purchase_dir = self.test_dir / "purchase_history"
        os.makedirs(purchase_dir, exist_ok=True)
        receipt_file = purchase_dir / "2026-09-27_receipt.txt"
        receipt_file.write_text("TCGplayer\nDate: 2026-09-27\nTotal: $50.00\n", encoding="utf-8")

        ledger_file = str(self.test_dir / "purchase_history.csv")
        cache_file = str(self.test_dir / "purchase_history_cache.json")
        report_file = str(self.test_dir / "TEST_REPORT.md")

        test_args = [
            "run_tracker.py",
            "--collection", str(self.collection_csv),
            "--db", self.db_path,
            "--prices", self.price_dir,
            "--purchase-dir", str(purchase_dir),
            "--purchase-ledger", ledger_file,
            "--purchase-cache", cache_file,
            "--output", report_file,
        ]
        with patch.object(sys, "argv", test_args):
            run_tracker.main()

        # Verify DB snapshot was recalculated to the new cost basis
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT total_cost_basis, net_unrealized_gain FROM portfolio_daily_summary WHERE date = ?", (today,))
        row = cur.fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[0], 50.00)

        # Verify output report exists and reflects updated cost basis
        self.assertTrue(os.path.isfile(report_file))
        report_text = Path(report_file).read_text(encoding="utf-8")
        self.assertIn("$50.00", report_text)

    def test_resolve_price_snapshot_date_valid(self):
        price_file = self.test_dir / "valid_date.json"
        price_file.write_text('{"date": "2026-09-15", "prices": {}}', encoding="utf-8")
        date = run_tracker.resolve_price_snapshot_date(str(price_file), "2026-10-01")
        self.assertEqual(date, "2026-09-15")

    def test_resolve_price_snapshot_date_non_dict_fallback(self):
        price_file = self.test_dir / "list_data.json"
        price_file.write_text('[1, 2, 3]', encoding="utf-8")
        date = run_tracker.resolve_price_snapshot_date(str(price_file), "2026-10-01")
        self.assertEqual(date, "2026-10-01")

    def test_resolve_price_snapshot_date_missing_fallback(self):
        missing_file = str(self.test_dir / "does_not_exist.json")
        date = run_tracker.resolve_price_snapshot_date(missing_file, "2026-10-01")
        self.assertEqual(date, "2026-10-01")

    @patch("run_tracker.sync_cardnexus_collection")
    def test_resolve_and_sync_collection_24h_cache_hit(self, mock_sync_cn):
        parser = run_tracker.build_tracker_argument_parser()
        args = parser.parse_args([])
        cfg = self._fake_load_config()

        target_csv = self.test_dir / "active_collection.csv"
        target_csv.write_text("name,expansion,printNumber,finish,totalQtyOwned\nV,Beta,001,Standard,1\n", encoding="utf-8")
        fresh_time = time.time() - 1800
        os.utime(target_csv, (fresh_time, fresh_time))
        cfg.collection_csv = str(target_csv)

        result = run_tracker.resolve_and_sync_collection(args, cfg)
        self.assertIsInstance(result, run_tracker.CollectionSyncResult)
        self.assertEqual(result.collection_source, "CardNexus API (cached)")
        self.assertFalse(result.collection_updated)
        mock_sync_cn.assert_not_called()

    @patch("run_tracker.sync_cardnexus_collection")
    def test_resolve_and_sync_collection_network_fallback(self, mock_sync_cn):
        parser = run_tracker.build_tracker_argument_parser()
        args = parser.parse_args([])
        cfg = self._fake_load_config()

        target_csv = self.test_dir / "active_collection.csv"
        target_csv.write_text("name,expansion,printNumber,finish,totalQtyOwned\nV,Beta,001,Standard,1\n", encoding="utf-8")
        stale_time = time.time() - 90000
        os.utime(target_csv, (stale_time, stale_time))
        cfg.collection_csv = str(target_csv)

        mock_sync_cn.return_value = (False, None, 0)

        with patch("sys.stderr", new_callable=io.StringIO) as mock_stderr:
            result = run_tracker.resolve_and_sync_collection(args, cfg)

        self.assertIsInstance(result, run_tracker.CollectionSyncResult)
        self.assertEqual(result.collection_source, "CardNexus API (fallback)")
        self.assertFalse(result.collection_updated)
        self.assertIn("Warning: CardNexus API sync failed. Falling back to cached collection", mock_stderr.getvalue())

    @patch("run_tracker.sync_cardnexus_collection")
    def test_resolve_and_sync_collection_validation_error_halts(self, mock_sync_cn):
        parser = run_tracker.build_tracker_argument_parser()
        args = parser.parse_args([])
        cfg = self._fake_load_config()

        target_csv = self.test_dir / "active_collection.csv"
        target_csv.write_text("name,expansion,printNumber,finish,totalQtyOwned\nV,Beta,001,Standard,1\n", encoding="utf-8")
        stale_time = time.time() - 90000
        os.utime(target_csv, (stale_time, stale_time))
        cfg.collection_csv = str(target_csv)

        mock_sync_cn.side_effect = CollectionValidationError("Corrupt collection CSV data", errors=["Row 2: name is empty"])

        with self.assertRaises(SystemExit) as cm:
            run_tracker.resolve_and_sync_collection(args, cfg)
        self.assertEqual(cm.exception.code, 1)


class TestTrackerCLICashPurchase(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.purchase_dir = self.test_dir / "purchase_history"
        self.config_json = self.test_dir / "config.json"
        self.config_json.write_text(
            json.dumps({
                "collection_csv": str(self.test_dir / "collection.csv"),
                "purchase_history_dir": str(self.purchase_dir),
            }),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_add_cash_non_interactive_success(self):
        argv = [
            "--config", str(self.config_json),
            "add-cash",
            "--date", "2026-09-15",
            "--amount", "35.00",
            "--merchant", "Local Game Store",
            "--description", "3x booster packs",
        ]
        with self.assertRaises(SystemExit) as cm:
            run_tracker.main(argv)
        self.assertEqual(cm.exception.code, 0)

        created_file = self.purchase_dir / "cash_2026-09-15_local_game_store.json"
        self.assertTrue(created_file.is_file())
        with open(created_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["date"], "2026-09-15")
        self.assertEqual(data["amount"], 35.00)
        self.assertEqual(data["merchant"], "Local Game Store")
        self.assertEqual(data["description"], "3x booster packs")
        self.assertTrue(data["cash"])

    def test_add_cash_with_preceding_root_flags(self):
        argv = [
            "--purchase-dir", str(self.purchase_dir),
            "add-cash",
            "--amount", "42.50",
            "--merchant", "Downtown Cards",
        ]
        with self.assertRaises(SystemExit) as cm:
            run_tracker.main(argv)
        self.assertEqual(cm.exception.code, 0)

        created_files = list(self.purchase_dir.glob("cash_*_downtown_cards.json"))
        self.assertEqual(len(created_files), 1)

    def test_add_cash_interactive_prompts(self):
        argv = ["--config", str(self.config_json), "add-cash"]
        with patch("sys.stdin.isatty", return_value=True), patch("builtins.input", side_effect=["2026-09-16", "25.00", "Corner Shop", "Draft Entry"]):
            with self.assertRaises(SystemExit) as cm:
                run_tracker.main(argv)
            self.assertEqual(cm.exception.code, 0)

        created_file = self.purchase_dir / "cash_2026-09-16_corner_shop.json"
        self.assertTrue(created_file.is_file())
        with open(created_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["amount"], 25.00)
        self.assertEqual(data["merchant"], "Corner Shop")
        self.assertEqual(data["description"], "Draft Entry")

    def test_add_cash_interactive_date_retry_loop(self):
        argv = ["--config", str(self.config_json), "add-cash"]
        with patch("sys.stdin.isatty", return_value=True), patch(
            "builtins.input",
            side_effect=["asd", "2026-09-16", "25.00", "Corner Shop", "Draft Entry"],
        ):
            with self.assertRaises(SystemExit) as cm:
                run_tracker.main(argv)
            self.assertEqual(cm.exception.code, 0)

        created_file = self.purchase_dir / "cash_2026-09-16_corner_shop.json"
        self.assertTrue(created_file.is_file())
        with open(created_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["date"], "2026-09-16")
        self.assertEqual(data["amount"], 25.00)

    def test_add_cash_interactive_defaults(self):
        argv = ["--config", str(self.config_json), "add-cash"]
        today_str = datetime.datetime.now().astimezone().strftime("%Y-%m-%d")
        with patch("sys.stdin.isatty", return_value=True), patch(
            "builtins.input",
            side_effect=["", "30.00", "", ""],
        ):
            with self.assertRaises(SystemExit) as cm:
                run_tracker.main(argv)
            self.assertEqual(cm.exception.code, 0)

        created_file = self.purchase_dir / f"cash_{today_str}_cash_purchase.json"
        self.assertTrue(created_file.is_file())
        with open(created_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["date"], today_str)
        self.assertEqual(data["amount"], 30.00)
        self.assertEqual(data["merchant"], "Cash Purchase")
        self.assertEqual(data["description"], f"Cash Purchase Purchase ({today_str})")

    def test_add_cash_missing_amount_non_interactive_error(self):
        argv = ["--config", str(self.config_json), "add-cash", "--date", "2026-09-15"]
        with patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                run_tracker.main(argv)
            self.assertEqual(cm.exception.code, 1)

    def test_add_cash_collision_resolution(self):
        self.purchase_dir.mkdir(parents=True, exist_ok=True)
        file1 = self.purchase_dir / "cash_2026-09-15_local_game_store.json"
        file1.write_text('{"existing": true}', encoding="utf-8")

        argv = [
            "--config", str(self.config_json),
            "add-cash",
            "--date", "2026-09-15",
            "--amount", "10.00",
            "--merchant", "Local Game Store",
        ]
        with self.assertRaises(SystemExit) as cm:
            run_tracker.main(argv)
        self.assertEqual(cm.exception.code, 0)

        file2 = self.purchase_dir / "cash_2026-09-15_local_game_store_2.json"
        self.assertTrue(file2.is_file())

    def test_drag_and_drop_csv_not_broken_by_subcommand(self):
        collection_file = self.test_dir / "drag_and_drop_collection.csv"
        collection_file.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned\nV,Beta,001,Standard,1\n",
            encoding="utf-8",
        )
        with patch("run_tracker.generate_portfolio_report") as mock_report:
            run_tracker.main([str(collection_file), "--report-only"])
            mock_report.assert_called_once()


if __name__ == "__main__":
    unittest.main()



