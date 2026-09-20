"""
Tests for valuation diagnostic console output: catalog match rate, unmatched items,
zero-price notices, and expansion cross-reference warnings.
"""

import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tracker.valuation import calculate_portfolio_valuation


def _write_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f)


class TestValuationDiagnostics(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.price_dir = str(self.root / "prices")
        self.db_path = str(self.root / "test.db")
        self.collection_path = str(self.root / "collection.csv")
        os.makedirs(self.price_dir, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_price_file(self, date_str: str, prices: dict, cards_catalog: dict = None):
        """Write a prices/{date}.json file and optional cards.json."""
        payload = {
            "date": date_str,
            "prices": prices,
        }
        _write_json(os.path.join(self.price_dir, f"{date_str}.json"), payload)
        if cards_catalog is not None:
            _write_json(os.path.join(self.price_dir, "cards.json"), cards_catalog)

    def _write_collection(self, content: str):
        Path(self.collection_path).write_text(content.strip(), encoding="utf-8")

    def _run_valuation(self, date_str: str, collection_rows: list) -> str:
        """Run valuation and capture stdout."""
        buf = io.StringIO()
        with patch("sys.stdout", buf):
            calculate_portfolio_valuation(
                date_str=date_str,
                collection_path=self.collection_path,
                cache_dir=self.price_dir,
                db_path=self.db_path,
                force=True,
                collection_rows=collection_rows,
            )
        return buf.getvalue()

    def test_match_rate_summary_all_matched(self):
        date_str = "2026-09-01"
        catalog = {
            "1001": {
                "productId": 1001,
                "name": "Towerfall",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B034",
                "rarity": "Common",
            }
        }
        prices = {"1001": {"Normal": {"marketPrice": 1.50}}}
        self._write_price_file(date_str, prices, catalog)
        rows = [{
            "name": "Towerfall", "expansion": "Welcome to Night City - Beta",
            "printNumber": "B034", "finish": "Standard", "totalQtyOwned": 1,
            "price": 0.0, "acquisitionDate": "2026-09-01", "item_type": "Card",
        }]
        output = self._run_valuation(date_str, rows)
        self.assertIn("Matched 1/1 collection rows to catalog products (100.0%)", output)
        self.assertNotIn("Warning:", output)

    def test_unmatched_items_warning(self):
        date_str = "2026-09-01"
        # Price file has no entries matching the collection row
        catalog = {"_groups": {}}
        prices = {}
        self._write_price_file(date_str, prices, catalog)
        rows = [{
            "name": "Ghost Card", "expansion": "Unknown Set",
            "printNumber": "X001", "finish": "Standard", "totalQtyOwned": 1,
            "price": 0.0, "acquisitionDate": "2026-09-01", "item_type": "Card",
        }]
        output = self._run_valuation(date_str, rows)
        self.assertIn("Matched 0/1 collection rows to catalog products (0.0%)", output)
        self.assertIn("Warning: 1 collection row(s) had no catalog match", output)
        self.assertIn("'Ghost Card'", output)

    def test_zero_price_notice(self):
        date_str = "2026-09-01"
        # Catalog matches but no price data for the product
        catalog = {
            "2001": {
                "productId": 2001,
                "name": "Priceless Card",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B099",
                "rarity": "Rare",
            }
        }
        prices = {}  # No price entry for product 2001
        self._write_price_file(date_str, prices, catalog)
        rows = [{
            "name": "Priceless Card", "expansion": "Welcome to Night City - Beta",
            "printNumber": "B099", "finish": "Standard", "totalQtyOwned": 1,
            "price": 0.0, "acquisitionDate": "2026-09-01", "item_type": "Card",
        }]
        output = self._run_valuation(date_str, rows)
        self.assertIn("Notice: 1 item(s) had no market price and were excluded from the portfolio total", output)
        self.assertIn("'Priceless Card'", output)

    def test_expansion_cross_reference_warning(self):
        date_str = "2026-09-01"
        catalog = {
            "3001": {
                "productId": 3001,
                "name": "Some Card",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B001",
                "rarity": "Common",
            }
        }
        prices = {}
        self._write_price_file(date_str, prices, catalog)
        # Use a completely unknown expansion with a card name that doesn't exist in catalog
        rows = [{
            "name": "Totally Unknown Card", "expansion": "Nonexistent Expansion Set",
            "printNumber": "X001", "finish": "Standard", "totalQtyOwned": 1,
            "price": 0.0, "acquisitionDate": "2026-09-01", "item_type": "Card",
        }]
        output = self._run_valuation(date_str, rows)
        self.assertIn("Warning: Expansion 'Nonexistent Expansion Set' not found in price catalog groups", output)

    def test_match_rate_with_partial_match(self):
        date_str = "2026-09-01"
        catalog = {
            "4001": {
                "productId": 4001,
                "name": "Known Card",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B010",
                "rarity": "Common",
            }
        }
        prices = {"4001": {"Normal": {"marketPrice": 2.00}}}
        self._write_price_file(date_str, prices, catalog)
        rows = [
            {
                "name": "Known Card", "expansion": "Welcome to Night City - Beta",
                "printNumber": "B010", "finish": "Standard", "totalQtyOwned": 1,
                "price": 0.0, "acquisitionDate": "2026-09-01", "item_type": "Card",
            },
            {
                "name": "Unknown Card", "expansion": "Welcome to Night City - Beta",
                "printNumber": "B999", "finish": "Standard", "totalQtyOwned": 1,
                "price": 0.0, "acquisitionDate": "2026-09-01", "item_type": "Card",
            },
        ]
        output = self._run_valuation(date_str, rows)
        self.assertIn("Matched 1/2 collection rows to catalog products (50.0%)", output)
        self.assertIn("Warning: 1 collection row(s) had no catalog match", output)


if __name__ == "__main__":
    unittest.main()
