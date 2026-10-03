"""
Unit tests for extracted valuation helper functions and ItemValuationContext.
"""

import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tracker.valuation import (
    ItemValuationContext,
    load_price_catalog,
    deduplicate_and_merge_items,
    match_collection_item,
    resolve_item_market_price,
    resolve_and_persist_baseline,
    calculate_l7d_metrics,
    persist_portfolio_summary,
    init_database,
)


class TestValuationHelpers(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.db_path = str(self.test_dir / "test_helpers.db")
        self.cache_dir = str(self.test_dir / "prices")
        os.makedirs(self.cache_dir, exist_ok=True)
        self.conn = init_database(self.db_path)
        self.cur = self.conn.cursor()

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()

    def test_load_price_catalog_indexing(self):
        price_data = {
            "date": "2026-10-01",
            "products": {
                "101": {
                    "productId": 101,
                    "name": "Johnny Silverhand",
                    "cleanName": "Johnny Silverhand",
                    "groupName": "Night City",
                    "printNumber": "001",
                    "prices": {"Normal": {"marketPrice": 25.50}},
                },
                "102": {
                    "productId": 102,
                    "name": "Arasaka Tower Booster Box",
                    "cleanName": "Arasaka Tower Booster Box",
                    "groupName": "Night City",
                    "prices": {"Normal": {"marketPrice": 120.00}},
                },
            },
        }
        price_file = os.path.join(self.cache_dir, "2026-10-01.json")
        with open(price_file, "w", encoding="utf-8") as f:
            json.dump(price_data, f)

        prods, indexes = load_price_catalog(self.cache_dir, "2026-10-01")
        self.assertEqual(len(prods), 2)
        self.assertIn(("night city", "001"), indexes["by_group_pnum"])
        self.assertIn(101, indexes["by_pid"])
        self.assertIn(("night city", "arasaka tower booster box"), indexes["by_group_name"])

    def test_load_price_catalog_fallback_and_missing(self):
        # 1. Fallback to repo prices/ directory when cache_dir lacks the date file
        empty_cache_dir = str(self.test_dir / "empty_cache")
        os.makedirs(empty_cache_dir, exist_ok=True)
        prods, indexes = load_price_catalog(empty_cache_dir, "2026-09-11")
        self.assertGreater(len(prods), 0)

        # 2. FileNotFoundError raised when date file exists in neither candidate path
        with self.assertRaises(FileNotFoundError):
            load_price_catalog(empty_cache_dir, "1999-01-01")

    def test_deduplicate_and_merge_items(self):
        collection_rows = [
            {"name": "Booster Box", "expansion": "Night City", "item_type": "Sealed", "totalQtyOwned": 2},
            {"name": "V", "expansion": "Night City", "printNumber": "002", "item_type": "Card", "totalQtyOwned": 1},
        ]
        sealed_rows = [
            {"name": "Booster Box", "expansion": "Night City", "totalQtyOwned": 2},  # Duplicate
            {"name": "Starter Deck", "expansion": "Night City", "totalQtyOwned": 1},  # Unique
        ]
        card_items, sealed_items, all_items = deduplicate_and_merge_items(collection_rows, sealed_rows)
        self.assertEqual(len(card_items), 2)
        self.assertEqual(len(sealed_items), 2)
        self.assertEqual(len(all_items), 3)  # Booster Box deduplicated

    def test_match_collection_item_and_context(self):
        indexes = {
            "by_group_pnum": {
                ("night city", "001"): {
                    "productId": 101,
                    "name": "Johnny Silverhand (Epic)",
                    "groupName": "Night City",
                    "printNumber": "001",
                    "rarity": "Epic",
                    "color": "Yellow",
                    "cardType": "Character",
                }
            },
            "by_group_name": {},
            "by_group_clean_name": {},
            "by_name": {},
            "by_pid": {},
        }
        row = {
            "name": "Johnny Silverhand",
            "expansion": "Night City",
            "printNumber": "001",
            "finish": "Standard",
            "totalQtyOwned": 3,
            "acquisitionDate": "2026-09-01",
        }
        prod, ctx, distinct_key, is_matched, label = match_collection_item(
            row, indexes, "2026-10-01", "2026-08-01", self.cur
        )
        self.assertTrue(is_matched)
        self.assertIsInstance(ctx, ItemValuationContext)
        self.assertEqual(ctx.card_key, "Night City::001::Standard::2026-09-01")
        self.assertEqual(ctx.name, "Johnny Silverhand")
        self.assertEqual(ctx.rarity, "Epic")
        self.assertEqual(ctx.color, "Yellow")
        self.assertEqual(ctx.qty, 3)

    def test_resolve_item_market_price_fallbacks(self):
        ctx = ItemValuationContext(
            card_key="Night City::001::Standard::2026-09-01",
            item_type="Card",
            prod_id=101,
            name="Johnny Silverhand",
            print_number="001",
            expansion="Night City",
            finish="Standard",
            rarity="Epic",
            color="Yellow",
            card_type="Character",
            effective_acq_date="2026-09-01",
            fallback_price=15.0,
            qty=2,
        )
        # 1. Product price exists
        prod = {"prices": {"Normal": {"marketPrice": 20.0, "lowPrice": 18.0, "highPrice": 22.0}}}
        market_p, low_p, mid_p, high_p, total_p = resolve_item_market_price(prod, ctx, "2026-10-01", self.cur)
        self.assertEqual(market_p, 20.0)
        self.assertEqual(total_p, 40.0)

        # 2. Product has no prices -> DB carry-forward
        self.cur.execute("""
        INSERT INTO card_metadata (card_key, product_id, name, print_number, expansion, finish)
        VALUES ('Night City::001::Standard::2026-08-01', 101, 'Johnny Silverhand', '001', 'Night City', 'Standard')
        """)
        self.cur.execute("""
        INSERT INTO daily_snapshots (date, card_key, quantity, unit_market_price, line_total)
        VALUES ('2026-09-30', 'Night City::001::Standard::2026-08-01', 1, 18.50, 18.50)
        """)
        market_p2, _, _, _, total_p2 = resolve_item_market_price(None, ctx, "2026-10-01", self.cur)
        self.assertEqual(market_p2, 18.50)
        self.assertEqual(total_p2, 37.0)

    def test_resolve_and_persist_baseline(self):
        ctx = ItemValuationContext(
            card_key="Night City::001::Standard::2026-09-01",
            item_type="Card",
            prod_id=101,
            name="Johnny Silverhand",
            print_number="001",
            expansion="Night City",
            finish="Standard",
            rarity="Epic",
            color="Yellow",
            card_type="Character",
            effective_acq_date="2026-09-01",
            fallback_price=12.0,
            qty=1,
        )
        cache_session = {}
        base_price = resolve_and_persist_baseline(
            self.cur, ctx, "2026-10-01", market_price=22.0, cache_dir=self.cache_dir, price_cache=cache_session
        )
        self.assertEqual(base_price, 22.0)
        self.cur.execute("SELECT baseline_market_price, rarity FROM card_metadata WHERE card_key = ?", (ctx.card_key,))
        row = self.cur.fetchone()
        self.assertEqual(row[0], 22.0)
        self.assertEqual(row[1], "Epic")

    def test_calculate_l7d_metrics_and_summary_persistence(self):
        self.cur.execute("""
        INSERT INTO portfolio_daily_summary (date, total_value, total_cards, unique_items)
        VALUES ('2026-09-24', 100.0, 10, 5)
        """)
        self.cur.execute("""
        INSERT INTO daily_snapshots (date, card_key, quantity, unit_market_price, baseline_price)
        VALUES ('2026-09-24', 'card_1', 2, 10.0, 10.0),
               ('2026-10-01', 'card_1', 2, 15.0, 10.0)
        """)
        dollar_delta, pct_delta = calculate_l7d_metrics(self.cur, "2026-10-01")
        self.assertEqual(dollar_delta, 10.0)  # (15 - 10) * 2
        self.assertEqual(pct_delta, 50.0)

        persist_portfolio_summary(
            self.cur, self.conn, "2026-10-01",
            total_value=110.0, total_cards=2, unique_items=1,
            l7d_dollar_delta=dollar_delta, l7d_pct_delta=pct_delta,
            total_lifetime_gain=10.0, lifetime_pct_gain=10.0,
            total_sealed=0, collection_updated_at=None, collection_source="CSV",
            total_cost_basis=80.0, net_unrealized_gain=30.0, net_unrealized_pct=37.5,
            purchases_updated_at=None,
        )
        self.cur.execute("SELECT total_value, net_unrealized_gain FROM portfolio_daily_summary WHERE date = '2026-10-01'")
        summary = self.cur.fetchone()
        self.assertEqual(summary[0], 110.0)
        self.assertEqual(summary[1], 30.0)


if __name__ == "__main__":
    unittest.main()
