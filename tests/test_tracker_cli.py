"""
Automated unit tests for CLI collection source resolution and orchestrator propagation.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import run_tracker


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

    def tearDown(self):
        self.temp_dir.cleanup()

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.sync_market_prices")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_standard_run_uses_collection_csv_filename(self, mock_calc, mock_sync, mock_report):
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
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_import_file_uses_imported_filename(self, mock_calc, mock_sync, mock_report):
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

        mock_calc.assert_called_once()
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_source"), "CSV (export_september.csv)")

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.sync_market_prices")
    @patch("run_tracker.sync_cardnexus_collection")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_sync_collection_uses_cardnexus_api_label(self, mock_calc, mock_sync_cn, mock_sync_prices, mock_report):
        mock_sync_prices.return_value = self.dummy_price_file
        mock_sync_cn.return_value = (True, str(self.collection_csv), 1)

        test_args = [
            "run_tracker.py",
            "--collection", str(self.collection_csv),
            "--sync-collection",
            "--db", self.db_path,
            "--prices", self.price_dir,
        ]
        with patch.dict(os.environ, {"CARDNEXUS_API_KEY": "test_api_key_123"}), \
             patch.object(sys, "argv", test_args):
            run_tracker.main()

        mock_calc.assert_called_once()
        self.assertEqual(mock_calc.call_args.kwargs.get("collection_source"), "CardNexus API")

    @patch("run_tracker.generate_portfolio_report")
    @patch("run_tracker.backfill_market_prices")
    @patch("run_tracker.calculate_portfolio_valuation")
    def test_backfill_propagates_collection_source(self, mock_calc, mock_backfill, mock_report):
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
