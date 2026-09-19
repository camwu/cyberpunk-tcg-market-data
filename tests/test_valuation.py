"""
Automated unit tests for portfolio valuation and catalog matching.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tracker.valuation import (
    calculate_portfolio_valuation,
    init_database,
    clear_historical_price_cache,
    get_historical_market_price,
)
from tracker.report import format_rarity, RARITY_ICONS


class TestPortfolioValuation(unittest.TestCase):

    def setUp(self):
        clear_historical_price_cache()
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.db_path = str(self.test_dir / "test_price_history.db")
        self.cache_dir = str(self.test_dir / "prices")
        os.makedirs(self.cache_dir, exist_ok=True)

    def tearDown(self):
        clear_historical_price_cache()
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
        try:
            cur = conn.cursor()
            cur.execute("SELECT card_key, baseline_price, line_total FROM daily_snapshots WHERE date = '2026-10-01' ORDER BY card_key")
            snapshots = cur.fetchall()
            self.assertEqual(len(snapshots), 2)
            self.assertEqual(snapshots[0][0], "SEALED::Welcome to Night City - Beta::714346::2026-09-11")
            self.assertEqual(snapshots[0][1], 180.00)
            self.assertEqual(snapshots[1][0], "SEALED::Welcome to Night City - Beta::714346::2026-10-01")
            self.assertEqual(snapshots[1][1], 240.00)
        finally:
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

    def test_unified_valuation_with_sealed_and_clamped_date(self):
        # Earliest price history available is 2026-09-11
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
                "cleanName": "Welcome to Night City Beta Booster Box",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": None,
                "rarity": None,
            }
        }
        with open(os.path.join(self.cache_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards_catalog, f)

        # Single-file collection rows containing both card and sealed item (with CardNexus ID 251426)
        collection_rows = [
            {
                "name": "V - Corporate Exile",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "006",
                "finish": "Standard",
                "totalQtyOwned": 1,
                "price": 2.50,
                "item_type": "Card",
                "acquisitionDate": "2026-09-02",  # Predates 2026-09-11
            },
            {
                "name": "Welcome to Night City - Beta Booster Box",
                "expansion": "Welcome to Night City - Beta",
                "productId": 251426,  # CardNexus ID
                "printNumber": None,
                "finish": "Standard",
                "totalQtyOwned": 1,
                "price": 216.08,
                "item_type": "Sealed",
                "acquisitionDate": "2026-09-02",  # Predates 2026-09-11
            },
        ]

        res = calculate_portfolio_valuation(
            date_str="2026-09-11",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )

        self.assertEqual(res["total_cards"], 1)
        self.assertEqual(res["total_sealed"], 1)
        self.assertEqual(res["unique_items"], 2)
        self.assertEqual(res["total_value"], 218.58)

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        # Verify sealed item matched TCGplayer ID 714346 and clamped date to 2026-09-11
        cur.execute("SELECT card_key, product_id, first_seen_date, item_type FROM card_metadata WHERE item_type = 'Sealed'")
        sealed_meta = cur.fetchone()
        self.assertEqual(sealed_meta[0], "SEALED::Welcome to Night City - Beta::714346::2026-09-11")
        self.assertEqual(sealed_meta[1], 714346)
        self.assertEqual(sealed_meta[2], "2026-09-11")
        self.assertEqual(sealed_meta[3], "Sealed")

        # Verify card clamped first_seen_date to 2026-09-11
        cur.execute("SELECT first_seen_date FROM card_metadata WHERE product_id = 101")
        self.assertEqual(cur.fetchone()[0], "2026-09-11")

        conn.close()

    def test_unified_valuation_missing_date_defaults_to_snapshot_date(self):
        daily_prices = {
            "date": "2026-09-15",
            "prices": {
                "714346": {"Normal": {"marketPrice": 240.00}},
            }
        }
        with open(os.path.join(self.cache_dir, "2026-09-15.json"), "w", encoding="utf-8") as f:
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

        # Sealed row without notes or acquisitionDate
        collection_rows = [
            {
                "name": "Welcome to Night City - Beta Booster Box",
                "expansion": "Welcome to Night City - Beta",
                "productId": 251426,
                "printNumber": None,
                "finish": "Standard",
                "totalQtyOwned": 1,
                "price": 240.00,
                "item_type": "Sealed",
            }
        ]

        res = calculate_portfolio_valuation(
            date_str="2026-09-15",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )

        self.assertEqual(res["total_sealed"], 1)
        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT card_key, first_seen_date FROM card_metadata WHERE item_type = 'Sealed'")
        meta = cur.fetchone()
        self.assertEqual(meta[0], "SEALED::Welcome to Night City - Beta::714346::2026-09-15")
        self.assertEqual(meta[1], "2026-09-15")
        conn.close()

    def test_unpriced_card_stores_null_and_maintains_l7d_parity(self):
        # Catalog with 1 priced card (101) and 1 unpriced card (202)
        cards_catalog = {
            "101": {
                "productId": 101,
                "name": "Johnny Silverhand",
                "cleanName": "Johnny Silverhand",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "001",
                "rarity": "Iconic",
            },
            "202": {
                "productId": 202,
                "name": "MT0D12 Flathead",
                "cleanName": "MT0D12 Flathead",
                "groupName": "The Heist - Beta Starter Deck",
                "printNumber": "B015",
                "rarity": "Uncommon",
            },
        }
        with open(os.path.join(self.cache_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards_catalog, f)

        collection_rows = [
            {
                "name": "Johnny Silverhand",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "001",
                "finish": "Foil",
                "totalQtyOwned": 1,
                "price": 0.0,
            },
            {
                "name": "MT0D12 Flathead",
                "expansion": "The Heist - Beta Starter Deck",
                "printNumber": "B015",
                "finish": "Standard",
                "totalQtyOwned": 2,
                "price": 0.0,
            },
        ]

        # Day 1: 101 is $10.00, 202 has no market price (empty prices dict)
        with open(os.path.join(self.cache_dir, "2026-09-11.json"), "w", encoding="utf-8") as f:
            json.dump({
                "date": "2026-09-11",
                "prices": {
                    "101": {"Foil": {"marketPrice": 10.00}},
                    "202": {},
                },
            }, f)

        res_d1 = calculate_portfolio_valuation(
            date_str="2026-09-11",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )
        self.assertEqual(res_d1["total_value"], 10.00)
        self.assertEqual(res_d1["total_cards"], 3)
        self.assertEqual(res_d1["lifetime_dollar_gain"], 0.0)

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("""
        SELECT unit_market_price, line_total, baseline_price, lifetime_gain_dollar, lifetime_gain_pct
        FROM daily_snapshots
        WHERE date = '2026-09-11' AND card_key = 'The Heist - Beta Starter Deck::B015::Standard::2026-09-11'
        """)
        unpriced_snapshot = cur.fetchone()
        self.assertIsNone(unpriced_snapshot[0])
        self.assertIsNone(unpriced_snapshot[1])
        self.assertIsNone(unpriced_snapshot[2])
        self.assertIsNone(unpriced_snapshot[3])
        self.assertIsNone(unpriced_snapshot[4])

        # Day 3: 101 rises to $15.00 (+5.00). 202 discovers first price at $4.00 (baseline $4.00, gain 0.0)
        with open(os.path.join(self.cache_dir, "2026-09-13.json"), "w", encoding="utf-8") as f:
            json.dump({
                "date": "2026-09-13",
                "prices": {
                    "101": {"Foil": {"marketPrice": 15.00}},
                    "202": {"Normal": {"marketPrice": 4.00}},
                },
            }, f)

        res_d2 = calculate_portfolio_valuation(
            date_str="2026-09-13",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )
        # Total value: 15.00 + (2 * 4.00) = 23.00
        self.assertEqual(res_d2["total_value"], 23.00)
        self.assertEqual(res_d2["lifetime_dollar_gain"], 5.00)

        # Day 5: 101 is $12.00 (+2.00). 202 rises to $6.00 (+4.00 total gain: (6.00 - 4.00) * 2)
        with open(os.path.join(self.cache_dir, "2026-09-15.json"), "w", encoding="utf-8") as f:
            json.dump({
                "date": "2026-09-15",
                "prices": {
                    "101": {"Foil": {"marketPrice": 12.00}},
                    "202": {"Normal": {"marketPrice": 6.00}},
                },
            }, f)

        res_d3 = calculate_portfolio_valuation(
            date_str="2026-09-15",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )
        # Total value: 12.00 + (2 * 6.00) = 24.00
        self.assertEqual(res_d3["total_value"], 24.00)
        # Lifetime gain: +2.00 (from 101) + +4.00 (from 202) = +6.00
        self.assertEqual(res_d3["lifetime_dollar_gain"], 6.00)

        # In summary table, L7D delta must match Lifetime delta within initial 7-day window
        cur.execute("""
        SELECT l7d_dollar_delta, l7d_pct_delta, lifetime_dollar_gain, lifetime_pct_gain
        FROM portfolio_daily_summary
        WHERE date = '2026-09-15'
        """)
        sum_row = cur.fetchone()
        self.assertEqual(sum_row[0], 6.00)
        self.assertEqual(sum_row[2], 6.00)
        self.assertEqual(sum_row[0], sum_row[2])
        self.assertEqual(sum_row[1], sum_row[3])

        # Force re-running Day 1 must NOT leak the Day 3 established baseline (4.00) backward into Day 1
        res_d1_rerun = calculate_portfolio_valuation(
            date_str="2026-09-11",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )
        self.assertEqual(res_d1_rerun["total_value"], 10.00)
        cur.execute("""
        SELECT unit_market_price, baseline_price, lifetime_gain_dollar
        FROM daily_snapshots
        WHERE date = '2026-09-11' AND card_key = 'The Heist - Beta Starter Deck::B015::Standard::2026-09-11'
        """)
        rerun_snapshot = cur.fetchone()
        self.assertIsNone(rerun_snapshot[0])
        self.assertIsNone(rerun_snapshot[2])
        conn.close()

    def test_canonical_catalog_sourcing_and_suffix_sanitization(self):
        daily_prices = {
            "date": "2026-09-17",
            "prices": {
                "301": {"Foil": {"marketPrice": 2.50}},
                "302": {"Standard": {"marketPrice": 5.00}},
            }
        }
        with open(os.path.join(self.cache_dir, "2026-09-17.json"), "w", encoding="utf-8") as f:
            json.dump(daily_prices, f)

        cards_catalog = {
            "301": {
                "productId": 301,
                "name": "Johnny Silverhand - Never Stop Fighting (Epic)",
                "cleanName": "Johnny Silverhand Never Stop Fighting Epic",
                "groupId": 1000,
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B011",
                "rarity": "Epic",
                "color": "Red",
            },
            "302": {
                "productId": 302,
                "name": "Viktor Vektor - Drop Your Illusions",
                "cleanName": "Viktor Vektor Drop Your Illusions",
                "groupId": 1000,
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B057",
                "rarity": "Epic",
                "color": "Yellow",
            },
        }
        with open(os.path.join(self.cache_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards_catalog, f)

        collection_rows = [
            {
                "name": "Raw Johnny Name From CSV",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "B011",
                "finish": "Foil",
                "totalQtyOwned": 1,
                "price": 2.00,
                "color": "",
            },
            {
                "name": "Viktor Vektor - Drop Your Illusions",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "B057",
                "finish": "Standard",
                "totalQtyOwned": 2,
                "price": 4.50,
                "color": "",
            },
        ]

        res = calculate_portfolio_valuation(
            date_str="2026-09-17",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT card_key, name, rarity, color FROM card_metadata ORDER BY print_number")
        rows = cur.fetchall()
        conn.close()

        self.assertEqual(rows[0][0], "Welcome to Night City - Beta::B011::Foil::2026-09-17")
        self.assertEqual(rows[0][1], "Johnny Silverhand - Never Stop Fighting")
        self.assertEqual(rows[0][2], "Epic")
        self.assertEqual(rows[0][3], "Red")

        self.assertEqual(rows[1][0], "Welcome to Night City - Beta::B057::Standard::2026-09-17")
        self.assertEqual(rows[1][1], "Viktor Vektor - Drop Your Illusions")
        self.assertEqual(rows[1][2], "Epic")
        self.assertEqual(rows[1][3], "Yellow")

    def test_disjoint_namespace_print_number_precedence_over_product_id(self):
        # Setup catalog with an unrelated card whose productId happens to match CardNexus CSV's internal ID (999)
        cards_catalog = {
            "999": {
                "productId": 999,
                "name": "Wrong Unrelated Card",
                "cleanName": "Wrong Unrelated Card",
                "groupId": 9999,
                "groupName": "Unrelated Expansion",
                "printNumber": "999",
                "rarity": "Common",
                "color": "Green",
            },
            "714219": {
                "productId": 714219,
                "name": "Viktor Vektor - Drop Your Illusions",
                "cleanName": "Viktor Vektor Drop Your Illusions",
                "groupId": 1000,
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B057",
                "rarity": "Epic",
                "color": "Yellow",
            },
        }
        with open(os.path.join(self.cache_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards_catalog, f)

        daily_prices = {
            "date": "2026-09-17",
            "prices": {
                "714219": {"Standard": {"marketPrice": 2.50}},
                "999": {"Standard": {"marketPrice": 100.00}},
            },
        }
        with open(os.path.join(self.cache_dir, "2026-09-17.json"), "w", encoding="utf-8") as f:
            json.dump(daily_prices, f)

        collection_rows = [
            {
                "productId": "999",  # CardNexus internal ID sequence
                "name": "Viktor Vektor - Drop Your Illusions",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "B057",
                "finish": "Standard",
                "totalQtyOwned": 1,
                "price": 2.50,
            }
        ]

        calculate_portfolio_valuation(
            date_str="2026-09-17",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT card_key, product_id, name, color FROM card_metadata WHERE card_key = 'Welcome to Night City - Beta::B057::Standard::2026-09-17'")
        row = cur.fetchone()
        conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row[1], 714219)  # Must resolve to TCGplayer 714219, NOT CardNexus 999
        self.assertEqual(row[2], "Viktor Vektor - Drop Your Illusions")
        self.assertEqual(row[3], "Yellow")

    def test_multi_lot_separate_baselines_and_keys(self):
        cards_catalog = {
            "500": {
                "productId": 500,
                "name": "Towerfall",
                "cleanName": "Towerfall",
                "groupId": 1000,
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "B034",
                "rarity": "Rare",
                "color": "Blue",
            }
        }
        with open(os.path.join(self.cache_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards_catalog, f)

        # Historical price on 2026-09-02: $10.00
        hist_prices = {
            "date": "2026-09-02",
            "prices": {
                "500": {"Standard": {"marketPrice": 10.00}}
            }
        }
        with open(os.path.join(self.cache_dir, "2026-09-02.json"), "w", encoding="utf-8") as f:
            json.dump(hist_prices, f)

        # Current price on 2026-09-18: $15.00
        cur_prices = {
            "date": "2026-09-18",
            "prices": {
                "500": {"Standard": {"marketPrice": 15.00}}
            }
        }
        with open(os.path.join(self.cache_dir, "2026-09-18.json"), "w", encoding="utf-8") as f:
            json.dump(cur_prices, f)

        # 2 lots for B034: 1 acquired on 2026-09-02, 2 acquired on 2026-09-18
        collection_rows = [
            {
                "name": "Towerfall",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "B034",
                "finish": "Standard",
                "totalQtyOwned": 1,
                "price": 0.0,
                "acquisitionDate": "2026-09-02",
                "item_type": "Card",
            },
            {
                "name": "Towerfall",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "B034",
                "finish": "Standard",
                "totalQtyOwned": 2,
                "price": 0.0,
                "acquisitionDate": "2026-09-18",
                "item_type": "Card",
            },
        ]

        calculate_portfolio_valuation(
            date_str="2026-09-18",
            collection_path="",
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            force=True,
            collection_rows=collection_rows,
        )

        conn = sqlite3.connect(self.db_path)
        cur = conn.cursor()
        cur.execute("SELECT card_key, quantity, unit_market_price, baseline_price, line_total, lifetime_gain_dollar FROM daily_snapshots ORDER BY card_key")
        snapshots = cur.fetchall()
        cur.execute("SELECT lifetime_dollar_gain, total_value FROM portfolio_daily_summary WHERE date = '2026-09-18'")
        summary = cur.fetchone()
        conn.close()

        self.assertEqual(len(snapshots), 2)
        # Lot 1 (2026-09-02): baseline $10.00, current $15.00, line_total $15.00, gain +$5.00
        self.assertEqual(snapshots[0][0], "Welcome to Night City - Beta::B034::Standard::2026-09-02")
        self.assertEqual(snapshots[0][1], 1)
        self.assertEqual(snapshots[0][2], 15.00)
        self.assertEqual(snapshots[0][3], 10.00)
        self.assertEqual(snapshots[0][4], 15.00)
        self.assertEqual(snapshots[0][5], 5.00)

        # Lot 2 (2026-09-18): baseline $15.00, current $15.00, line_total $30.00, gain $0.00
        self.assertEqual(snapshots[1][0], "Welcome to Night City - Beta::B034::Standard::2026-09-18")
        self.assertEqual(snapshots[1][1], 2)
        self.assertEqual(snapshots[1][2], 15.00)
        self.assertEqual(snapshots[1][3], 15.00)
        self.assertEqual(snapshots[1][4], 30.00)
        self.assertEqual(snapshots[1][5], 0.00)

        # Portfolio totals: total value = $45.00, baseline cost = $40.00, gain = +$5.00
        self.assertEqual(summary[0], 5.00)
        self.assertEqual(summary[1], 45.00)

    def test_sqlite_migration_3part_to_4part_card_keys(self):
        migration_db = os.path.join(self.temp_dir.name, "migration_test.db")
        conn = sqlite3.connect(migration_db)
        cur = conn.cursor()
        cur.execute("""
        CREATE TABLE card_metadata (
            card_key TEXT PRIMARY KEY,
            product_id INTEGER,
            name TEXT,
            print_number TEXT,
            rarity TEXT,
            expansion TEXT,
            finish TEXT,
            first_seen_date TEXT,
            baseline_market_price REAL,
            color TEXT,
            item_type TEXT DEFAULT 'Card'
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
        # Insert 3-part card key
        cur.execute("""
        INSERT INTO card_metadata (card_key, product_id, name, print_number, rarity, expansion, finish, first_seen_date, baseline_market_price, item_type)
        VALUES ('Welcome to Night City - Beta::B011::Foil', 101, 'Johnny Silverhand', 'B011', 'Epic', 'Welcome to Night City - Beta', 'Foil', '2026-09-12', 20.00, 'Card')
        """)
        cur.execute("""
        INSERT INTO daily_snapshots (date, card_key, quantity, unit_market_price, line_total, baseline_price, lifetime_gain_dollar, lifetime_gain_pct)
        VALUES ('2026-09-12', 'Welcome to Night City - Beta::B011::Foil', 1, 20.00, 20.00, 20.00, 0.0, 0.0)
        """)
        conn.commit()
        conn.close()

        # Run init_database which triggers migration
        migrated_conn = init_database(migration_db)
        m_cur = migrated_conn.cursor()
        m_cur.execute("SELECT card_key FROM card_metadata")
        meta_keys = [r[0] for r in m_cur.fetchall()]
        m_cur.execute("SELECT card_key FROM daily_snapshots")
        snap_keys = [r[0] for r in m_cur.fetchall()]
        migrated_conn.close()

        self.assertEqual(meta_keys, ["Welcome to Night City - Beta::B011::Foil::2026-09-12"])
        self.assertEqual(snap_keys, ["Welcome to Night City - Beta::B011::Foil::2026-09-12"])

    def test_migration_derives_date_from_min_snapshots_when_first_seen_is_null(self):
        migration_db = str(self.test_dir / "migration_min_test.db")
        conn = sqlite3.connect(migration_db)
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
            baseline_market_price REAL,
            item_type TEXT
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
        # Insert 3-part card key with first_seen_date = NULL
        cur.execute("""
        INSERT INTO card_metadata (card_key, product_id, name, print_number, rarity, expansion, finish, first_seen_date, baseline_market_price, item_type)
        VALUES ('Welcome to Night City - Beta::B011::Foil', 101, 'Johnny Silverhand', 'B011', 'Epic', 'Welcome to Night City - Beta', 'Foil', NULL, 20.00, 'Card')
        """)
        # Multiple snapshots: oldest is 2026-09-05
        cur.execute("""
        INSERT INTO daily_snapshots (date, card_key, quantity, unit_market_price, line_total, baseline_price, lifetime_gain_dollar, lifetime_gain_pct)
        VALUES
        ('2026-09-05', 'Welcome to Night City - Beta::B011::Foil', 1, 18.00, 18.00, 18.00, 0.0, 0.0),
        ('2026-09-12', 'Welcome to Night City - Beta::B011::Foil', 1, 20.00, 20.00, 18.00, 2.0, 11.1)
        """)
        conn.commit()
        conn.close()

        # Run init_database which triggers migration
        migrated_conn = init_database(migration_db)
        m_cur = migrated_conn.cursor()
        m_cur.execute("SELECT card_key, first_seen_date FROM card_metadata")
        meta_rows = m_cur.fetchall()
        m_cur.execute("SELECT DISTINCT card_key FROM daily_snapshots")
        snap_keys = [r[0] for r in m_cur.fetchall()]
        migrated_conn.close()

        # Both keys should be migrated using MIN(date) = 2026-09-05, not hard-coded 2026-09-11
        expected_key = "Welcome to Night City - Beta::B011::Foil::2026-09-05"
        self.assertEqual(meta_rows, [(expected_key, "2026-09-05")])
        self.assertEqual(snap_keys, [expected_key])

    def test_get_historical_market_price_print_number_exclusivity(self):
        # Cache with multiple variants of the same card name but different print numbers
        cache_data = {
            "date": "2026-09-02",
            "products": {
                "101": {
                    "productId": 101,
                    "name": "Johnny Silverhand",
                    "groupName": "Welcome to Night City - Beta",
                    "printNumber": "B011",
                    "prices": {"Normal": {"marketPrice": 15.00}},
                },
                "102": {
                    "productId": 102,
                    "name": "Johnny Silverhand",
                    "groupName": "Welcome to Night City - Beta",
                    "printNumber": "P001",
                    "prices": {"Normal": {"marketPrice": 60.00}},
                },
                "201": {
                    "productId": 201,
                    "name": "Beta Booster Box",
                    "groupName": "Welcome to Night City - Beta",
                    "printNumber": None,
                    "prices": {"Normal": {"marketPrice": 220.00}},
                },
            },
        }
        with open(os.path.join(self.cache_dir, "2026-09-02.json"), "w", encoding="utf-8") as f:
            json.dump(cache_data, f)

        # Looking for printNumber B011 should match product 101
        p_b011 = get_historical_market_price(
            cache_dir=self.cache_dir,
            target_date="2026-09-02",
            prod_id=None,
            expansion="Welcome to Night City - Beta",
            print_number="B011",
            name="Johnny Silverhand",
            finish="Standard",
        )
        self.assertEqual(p_b011, 15.00)

        # Looking for an unmatched printNumber P999 should return None, NOT fall back to matching on name alone
        p_unmatched = get_historical_market_price(
            cache_dir=self.cache_dir,
            target_date="2026-09-02",
            prod_id=None,
            expansion="Welcome to Night City - Beta",
            print_number="P999",
            name="Johnny Silverhand",
            finish="Standard",
        )
        self.assertIsNone(p_unmatched)

        # Looking for sealed product with print_number=None matches on name
        p_sealed = get_historical_market_price(
            cache_dir=self.cache_dir,
            target_date="2026-09-02",
            prod_id=None,
            expansion="Welcome to Night City - Beta",
            print_number=None,
            name="Beta Booster Box",
            finish="Standard",
        )
        self.assertEqual(p_sealed, 220.00)


class TestReportFormatting(unittest.TestCase):

    def test_rarity_icons_canon(self):
        self.assertEqual(RARITY_ICONS["Common"], "▽")
        self.assertEqual(RARITY_ICONS["Uncommon"], "△")
        self.assertEqual(RARITY_ICONS["Rare"], "◇")
        self.assertEqual(RARITY_ICONS["Epic"], "◈")
        self.assertEqual(RARITY_ICONS["Secret"], "◆")
        self.assertEqual(RARITY_ICONS["Iconic"], "★")
        self.assertEqual(RARITY_ICONS["Nova"], "▣")

    def test_format_rarity(self):
        self.assertEqual(format_rarity("Common"), "▽ Common")
        self.assertEqual(format_rarity("Uncommon"), "△ Uncommon")
        self.assertEqual(format_rarity("Epic"), "◈ Epic")
        self.assertEqual(format_rarity("Secret"), "◆ Secret")
        self.assertEqual(format_rarity("Common", bold=True), "**▽ Common**")
        self.assertEqual(format_rarity("Uncommon", bold=True), "**△ Uncommon**")
        self.assertEqual(format_rarity("Epic", bold=True), "**◈ Epic**")
        self.assertEqual(format_rarity("Secret", bold=True), "**◆ Secret**")


if __name__ == "__main__":
    unittest.main()
