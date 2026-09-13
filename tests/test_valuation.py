"""
Automated unit tests for portfolio valuation and catalog matching.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from tracker.valuation import calculate_portfolio_valuation, init_database


class TestPortfolioValuation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.db_path = str(self.test_dir / "test_price_history.db")
        self.cache_dir = str(self.test_dir / "prices")
        os.makedirs(self.cache_dir, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_catalog_matching_with_root_print_number(self):
        # Create slim daily price cache
        daily_prices = {
            "date": "2026-09-13",
            "prices": {
                "101": {
                    "Normal": {"marketPrice": 2.50},
                    "Foil": {"marketPrice": 12.00},
                }
            }
        }
        with open(os.path.join(self.cache_dir, "2026-09-13.json"), "w", encoding="utf-8") as f:
            json.dump(daily_prices, f)

        # Create cards.json catalog with root-level printNumber (as produced by sync/scrape)
        cards_catalog = {
            "101": {
                "productId": 101,
                "name": "V - Corporate Exile",
                "cleanName": "V Corporate Exile",
                "groupId": 1000,
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "006",
                "rarity": "Nova",
            }
        }
        with open(os.path.join(self.cache_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards_catalog, f)

        collection_rows = [
            {
                "name": "V - Corporate Exile",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "006",
                "finish": "Foil",
                "totalQtyOwned": 2,
                "price": 5.00,  # fallback acquisition price
            }
        ]

        # Valuation should match productId 101 Foil ($12.00) rather than falling back to $5.00
        res = calculate_portfolio_valuation(
            date_str="2026-09-13",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )

        self.assertEqual(res["total_cards"], 2)
        self.assertEqual(res["unique_items"], 1)
        self.assertEqual(res["total_value"], 24.00)  # 2 * 12.00
        self.assertEqual(res["lifetime_dollar_gain"], 0.0)


if __name__ == "__main__":
    unittest.main()
