"""
Automated unit tests for CLI collection source resolution, automatic 24-hour
CardNexus API synchronization, cache evaluation, and orchestrator propagation.
"""

import io
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import run_tracker
from tracker.config import TrackerConfig


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
            output_report=kwargs.get("output_report") or "LATEST_PORTFOLIO_SUMMARY.md",
            sealed_csv=kwargs.get("sealed_csv"),
            cardnexus_api_key=self.api_key_for_test,
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

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.sync_market_prices")
    @patch("run_tracker.sync_cardnexus_collection")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_api_failure_falls_back_to_existing_collection(self, mock_calc, mock_sync_cn, mock_sync_prices, mock_report):
        mock_sync_prices.return_value = self.dummy_price_file
        mock_sync_cn.return_value = (False, None, 0)

        test_args = [
            "run_tracker.py",
            "--collection", str(self.collection_csv),
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
        self.assertIn("Warning: CardNexus API sync failed. Falling back to cached collection:", mock_stderr.getvalue())

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


if __name__ == "__main__":
    unittest.main()
