"""
Automated unit tests for portfolio valuation and catalog matching.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from tracker.valuation import calculate_portfolio_valuation, init_database
from tracker.report import format_rarity, RARITY_ICONS


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

    def test_establish_baseline_on_first_nonzero_price(self):
        # Day 1: Card has no market prices and fallback acquisition price is 0.00
        daily_prices_d1 = {
            "date": "2026-09-11",
            "prices": {
                "202": {}
            }
        }
        with open(os.path.join(self.cache_dir, "2026-09-11.json"), "w", encoding="utf-8") as f:
            json.dump(daily_prices_d1, f)

        cards_catalog = {
            "202": {
                "productId": 202,
                "name": "MT0D12 Flathead",
                "cleanName": "MT0D12 Flathead",
                "groupId": 2000,
                "groupName": "The Heist - Beta Starter Deck",
                "printNumber": "B015",
                "rarity": "Uncommon",
            }
        }
        with open(os.path.join(self.cache_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards_catalog, f)

        collection_rows = [
            {
                "name": "MT0D12 Flathead",
                "expansion": "The Heist - Beta Starter Deck",
                "printNumber": "B015",
                "finish": "Standard",
                "totalQtyOwned": 2,
                "price": 0.00,
            }
        ]

        # Run day 1 valuation
        res_d1 = calculate_portfolio_valuation(
            date_str="2026-09-11",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )
        self.assertEqual(res_d1["total_value"], 0.0)
        self.assertEqual(res_d1["lifetime_dollar_gain"], 0.0)

        # Day 2: First real market price arrives ($4.34)
        daily_prices_d2 = {
            "date": "2026-09-13",
            "prices": {
                "202": {
                    "Normal": {"marketPrice": 4.34}
                }
            }
        }
        with open(os.path.join(self.cache_dir, "2026-09-13.json"), "w", encoding="utf-8") as f:
            json.dump(daily_prices_d2, f)

        res_d2 = calculate_portfolio_valuation(
            date_str="2026-09-13",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )
        # Total value is 2 * 4.34 = 8.68
        self.assertEqual(res_d2["total_value"], 8.68)
        # Lifetime gain should establish baseline at 4.34 and remain 0.00, NOT +8.68 pure profit
        self.assertEqual(res_d2["lifetime_dollar_gain"], 0.0)


class TestReportFormatting(unittest.TestCase):

    def test_rarity_icons_canon(self):
        self.assertEqual(RARITY_ICONS["Common"], "▽")
        self.assertEqual(RARITY_ICONS["Uncommon"], "△")
        self.assertEqual(RARITY_ICONS["Rare"], "◇")
        self.assertEqual(RARITY_ICONS["Epic"], "🞚")
        self.assertEqual(RARITY_ICONS["Secret"], "⯁")
        self.assertEqual(RARITY_ICONS["Iconic"], "★")
        self.assertEqual(RARITY_ICONS["Nova"], "▣")

    def test_format_rarity(self):
        self.assertEqual(format_rarity("Common"), "▽ Common")
        self.assertEqual(format_rarity("Uncommon"), "△ Uncommon")
        self.assertEqual(format_rarity("Common", bold=True), "**▽ Common**")
        self.assertEqual(format_rarity("Uncommon", bold=True), "**△ Uncommon**")


if __name__ == "__main__":
    unittest.main()
