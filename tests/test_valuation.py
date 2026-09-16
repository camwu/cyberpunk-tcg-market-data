"""
Automated unit tests for portfolio valuation and catalog matching.
"""

import json
import os
import sqlite3
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

    def test_sealed_product_valuation(self):
        # Daily price cache with both card and booster box
        daily_prices = {
            "date": "2026-09-11",
            "prices": {
                "101": {"Normal": {"marketPrice": 2.50}},
                "714346": {"Normal": {"marketPrice": 216.08}},
            }
        }
        with open(os.path.join(self.cache_dir, "2026-09-11.json"), "w", encoding="utf-8") as f:
            json.dump(daily_prices, f)

        cards_catalog = {
            "101": {
                "productId": 101,
                "name": "V - Corporate Exile",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "006",
                "rarity": "Nova",
            },
            "714346": {
                "productId": 714346,
                "name": "Welcome to Night City - Beta Booster Box",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": None,
                "rarity": None,
            }
        }
        with open(os.path.join(self.cache_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards_catalog, f)

        collection_rows = [
            {
                "name": "V - Corporate Exile",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "006",
                "finish": "Standard",
                "totalQtyOwned": 2,
                "price": 0.0,
                "item_type": "Card",
            }
        ]

        sealed_rows = [
            {
                "productId": 714346,
                "name": "Welcome to Night City - Beta Booster Box",
                "expansion": "Welcome to Night City - Beta",
                "finish": "Standard",
                "totalQtyOwned": 1,
                "price": 0.0,
                "item_type": "Sealed",
            }
        ]

        res = calculate_portfolio_valuation(
            date_str="2026-09-11",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
            sealed_rows=sealed_rows,
        )

        self.assertEqual(res["total_cards"], 2)
        self.assertEqual(res["total_sealed"], 1)
        self.assertEqual(res["unique_items"], 2)
        # 2 * 2.50 + 1 * 216.08 = 221.08
        self.assertEqual(res["total_value"], 221.08)

        # Verify database records
        import sqlite3
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT item_type FROM card_metadata WHERE product_id = 714346")
        self.assertEqual(cur.fetchone()[0], "Sealed")

        cur.execute("SELECT item_type FROM card_metadata WHERE product_id = 101")
        self.assertEqual(cur.fetchone()[0], "Card")

        cur.execute("SELECT total_sealed, total_cards FROM portfolio_daily_summary WHERE date = '2026-09-11'")
        summary_row = cur.fetchone()
        self.assertEqual(summary_row[0], 1)
        self.assertEqual(summary_row[1], 2)
        conn.close()

    def test_sealed_product_honors_explicit_purchase_price(self):
        daily_prices = {
            "date": "2026-09-11",
            "prices": {
                "714346": {"Normal": {"marketPrice": 216.08}},
            }
        }
        with open(os.path.join(self.cache_dir, "2026-09-11.json"), "w", encoding="utf-8") as f:
            json.dump(daily_prices, f)

        cards_catalog = {
            "714346": {
                "productId": 714346,
                "name": "Welcome to Night City - Beta Booster Box",
                "groupName": "Welcome to Night City - Beta",
            }
        }
        with open(os.path.join(self.cache_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards_catalog, f)

        # User bought for $180.00 (below market price of 216.08)
        sealed_rows = [
            {
                "productId": 714346,
                "name": "Welcome to Night City - Beta Booster Box",
                "expansion": "Welcome to Night City - Beta",
                "finish": "Standard",
                "totalQtyOwned": 1,
                "price": 180.00,
                "acquisitionDate": "2026-09-11",
                "item_type": "Sealed",
            }
        ]

        res = calculate_portfolio_valuation(
            date_str="2026-09-11",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=[],
            sealed_rows=sealed_rows,
        )

        self.assertEqual(res["total_value"], 216.08)
        # Gain should be 216.08 - 180.00 = +36.08
        self.assertEqual(res["lifetime_dollar_gain"], 36.08)

    def test_sealed_product_subtype_iteration_fallback(self):
        # Product listed under non-standard subtype "Unopened"
        daily_prices = {
            "date": "2026-09-11",
            "prices": {
                "714346": {"Unopened": {"marketPrice": 220.00}},
            }
        }
        with open(os.path.join(self.cache_dir, "2026-09-11.json"), "w", encoding="utf-8") as f:
            json.dump(daily_prices, f)

        cards_catalog = {
            "714346": {
                "productId": 714346,
                "name": "Welcome to Night City - Beta Booster Box",
                "groupName": "Welcome to Night City - Beta",
            }
        }
        with open(os.path.join(self.cache_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards_catalog, f)

        sealed_rows = [
            {
                "productId": 714346,
                "name": "Welcome to Night City - Beta Booster Box",
                "expansion": "Welcome to Night City - Beta",
                "finish": "Standard",
                "totalQtyOwned": 1,
                "price": 0.0,
                "acquisitionDate": "2026-09-11",
                "item_type": "Sealed",
            }
        ]

        res = calculate_portfolio_valuation(
            date_str="2026-09-11",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=[],
            sealed_rows=sealed_rows,
        )

        # Discovered 220.00 from "Unopened" key
        self.assertEqual(res["total_value"], 220.00)

    def test_multiple_sealed_lots_isolated_baselines(self):
        daily_prices = {
            "date": "2026-10-01",
            "prices": {
                "714346": {"Normal": {"marketPrice": 235.17}},
            }
        }
        with open(os.path.join(self.cache_dir, "2026-10-01.json"), "w", encoding="utf-8") as f:
            json.dump(daily_prices, f)

        cards_catalog = {
            "714346": {
                "productId": 714346,
                "name": "Welcome to Night City - Beta Booster Box",
                "groupName": "Welcome to Night City - Beta",
            }
        }
        with open(os.path.join(self.cache_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards_catalog, f)

        sealed_rows = [
            {
                "productId": 714346,
                "name": "Welcome to Night City - Beta Booster Box",
                "expansion": "Welcome to Night City - Beta",
                "finish": "Standard",
                "totalQtyOwned": 1,
                "price": 180.00,
                "acquisitionDate": "2026-09-11",
                "item_type": "Sealed",
            },
            {
                "productId": 714346,
                "name": "Welcome to Night City - Beta Booster Box",
                "expansion": "Welcome to Night City - Beta",
                "finish": "Standard",
                "totalQtyOwned": 1,
                "price": 240.00,
                "acquisitionDate": "2026-10-01",
                "item_type": "Sealed",
            },
        ]

        res = calculate_portfolio_valuation(
            date_str="2026-10-01",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=[],
            sealed_rows=sealed_rows,
        )

        self.assertEqual(res["total_sealed"], 2)
        self.assertEqual(res["total_value"], round(2 * 235.17, 2))
        # Lot 1 gain: 235.17 - 180 = +55.17
        # Lot 2 gain: 235.17 - 240 = -4.83
        # Total gain = 55.17 - 4.83 = +50.34
        self.assertEqual(res["lifetime_dollar_gain"], 50.34)

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT card_key, baseline_price, line_total FROM daily_snapshots WHERE date = '2026-10-01' ORDER BY card_key")
        snapshots = cur.fetchall()
        self.assertEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0][0], "SEALED::Welcome to Night City - Beta::714346::2026-09-11")
        self.assertEqual(snapshots[0][1], 180.00)
        self.assertEqual(snapshots[1][0], "SEALED::Welcome to Night City - Beta::714346::2026-10-01")
        self.assertEqual(snapshots[1][1], 240.00)
        conn.close()

    def test_valuation_with_explicit_price_file(self):
        # Create a fallback latest.json that differs from target date filename
        fallback_file = os.path.join(self.cache_dir, "custom_fallback.json")
        with open(fallback_file, "w", encoding="utf-8") as f:
            json.dump({
                "date": "2026-09-14",
                "prices": {
                    "101": {
                        "Normal": {"marketPrice": 15.00},
                    }
                }
            }, f)

        # Catalog
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
                "finish": "Standard",
                "totalQtyOwned": 2,
            }
        ]

        res = calculate_portfolio_valuation(
            date_str="2026-09-14",
            collection_path=str(self.test_dir / "collection.csv"),
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
            price_file=fallback_file,
        )
        self.assertEqual(res["total_value"], 30.00)
        self.assertEqual(res["total_cards"], 2)

    def test_carry_forward_unlisted_market_price(self):
        # Day 1: Card is priced at $10.00
        daily_prices_d1 = {
            "date": "2026-09-13",
            "prices": {
                "303": {"Normal": {"marketPrice": 10.00}}
            }
        }
        with open(os.path.join(self.cache_dir, "2026-09-13.json"), "w", encoding="utf-8") as f:
            json.dump(daily_prices_d1, f)

        cards_catalog = {
            "303": {
                "productId": 303,
                "name": "Dexter DeShawn - One Last Chance",
                "cleanName": "Dexter DeShawn One Last Chance",
                "groupId": 2000,
                "groupName": "The Heist - Beta Starter Deck",
                "printNumber": "B016",
                "rarity": "Uncommon",
            }
        }
        with open(os.path.join(self.cache_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards_catalog, f)

        collection_rows = [
            {
                "name": "Dexter DeShawn - One Last Chance",
                "expansion": "The Heist - Beta Starter Deck",
                "printNumber": "B016",
                "finish": "Standard",
                "totalQtyOwned": 2,
                "price": 0.00,
            }
        ]

        res_d1 = calculate_portfolio_valuation(
            date_str="2026-09-13",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )
        self.assertEqual(res_d1["total_value"], 20.00)
        self.assertEqual(res_d1["lifetime_dollar_gain"], 0.0)

        # Day 2: Market scrape lacks pricing data for product 303 (unlisted / None)
        daily_prices_d2 = {
            "date": "2026-09-14",
            "prices": {
                "303": {"Normal": {"marketPrice": None}}
            }
        }
        with open(os.path.join(self.cache_dir, "2026-09-14.json"), "w", encoding="utf-8") as f:
            json.dump(daily_prices_d2, f)

        res_d2 = calculate_portfolio_valuation(
            date_str="2026-09-14",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )
        # Should carry forward previous market price ($10.00) instead of dropping to $0.00
        self.assertEqual(res_d2["total_value"], 20.00)
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
