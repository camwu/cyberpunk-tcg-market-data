"""
Unit tests for purchase history management, SHA-256 caching, and cost basis tracking.
Uses isolated temporary directories and synthetic mock fixtures only.
"""

import csv
import json
import os
from pathlib import Path
import tempfile
import unittest

from tracker.purchases import (
    PurchaseRecord,
    compute_file_sha256,
    parse_date_candidate,
    parse_receipt_document,
    load_purchase_ledger,
    save_purchase_ledger,
    load_purchase_cache,
    save_purchase_cache,
    sync_purchase_history,
)
from tracker.valuation import init_database, calculate_portfolio_valuation


class TestPurchaseHistory(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.purchase_dir = self.test_dir / "purchase_history"
        self.purchase_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = str(self.test_dir / "purchase_history_cache.json")
        self.ledger_path = str(self.test_dir / "purchase_history.csv")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_compute_file_sha256(self):
        dummy_file = self.purchase_dir / "sample.pdf"
        dummy_file.write_bytes(b"sample pdf receipt content")
        hash_val = compute_file_sha256(str(dummy_file))
        self.assertEqual(len(hash_val), 64)
        # Deterministic check
        self.assertEqual(hash_val, compute_file_sha256(str(dummy_file)))

    def test_parse_date_candidate(self):
        self.assertEqual(parse_date_candidate("Order_2026-04-17.pdf"), "2026-04-17")
        self.assertEqual(parse_date_candidate("Receipt from Apr 17, 2026 at 5:53 PM"), "2026-04-17")
        self.assertEqual(parse_date_candidate("Date: Sep 21, 2026"), "2026-09-21")
        self.assertIsNone(parse_date_candidate("no date in this string"))

    def test_parse_receipt_document_filename_amount_fallback(self):
        receipt_file = self.purchase_dir / "2026-09-18_PaperHeroGames_43.90.png"
        receipt_file.write_bytes(b"dummy image bytes")
        parsed = parse_receipt_document(str(receipt_file))
        self.assertIsNotNone(parsed)
        p_date, p_merchant, p_amount, p_desc = parsed
        self.assertEqual(p_date, "2026-09-18")
        self.assertEqual(p_merchant, "Paper Hero's Games")
        self.assertEqual(p_amount, 43.90)

    def test_ledger_save_and_load_roundtrip(self):
        rec1 = PurchaseRecord(
            date="2026-04-17",
            merchant="Kickstarter",
            amount=349.00,
            description="Starter Kit",
            filename="receipt1.pdf",
            sha256="abcd1234",
        )
        rec2 = PurchaseRecord(
            date="2026-09-11",
            merchant="TCGplayer",
            amount=32.78,
            description="Singles Lot",
            filename="receipt2.pdf",
            sha256="ef015678",
        )
        save_purchase_ledger(self.ledger_path, [rec1, rec2])

        loaded = load_purchase_ledger(self.ledger_path)
        self.assertEqual(len(loaded), 2)
        self.assertIn("receipt1.pdf", loaded)
        self.assertEqual(loaded["receipt1.pdf"].amount, 349.00)
        self.assertEqual(loaded["receipt2.pdf"].merchant, "TCGplayer")

    def test_sync_purchase_history_cache_and_idempotency(self):
        file1 = self.purchase_dir / "2026-04-17_Kickstarter_349.00.png"
        file1.write_bytes(b"invoice 1 content")

        file2 = self.purchase_dir / "2026-09-13_eBay_11.52.png"
        file2.write_bytes(b"invoice 2 content")

        # Initial sync: should parse and create cache + ledger
        total, records = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
        )
        self.assertEqual(total, 360.52)
        self.assertEqual(len(records), 2)
        self.assertTrue(os.path.isfile(self.cache_path))
        self.assertTrue(os.path.isfile(self.ledger_path))

        # Second sync: cache hit, zero re-parsing needed
        cache_data_before = load_purchase_cache(self.cache_path)
        total2, records2 = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
        )
        self.assertEqual(total2, 360.52)
        self.assertEqual(len(records2), 2)
        cache_data_after = load_purchase_cache(self.cache_path)
        self.assertEqual(cache_data_before["files"], cache_data_after["files"])

    def test_sync_purchase_history_ledger_override(self):
        file1 = self.purchase_dir / "2026-09-18_Event_Entry.png"
        file1.write_bytes(b"image bytes without clear amount")

        # Pre-seed ledger with manual user entry
        initial_rec = PurchaseRecord(
            date="2026-09-18",
            merchant="Paper Hero's Games",
            amount=43.90,
            description="Beta Event Entry",
            filename="2026-09-18_Event_Entry.png",
            sha256="",
        )
        save_purchase_ledger(self.ledger_path, [initial_rec])

        total, records = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
        )
        self.assertEqual(total, 43.90)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].amount, 43.90)
        self.assertEqual(records[0].merchant, "Paper Hero's Games")
        # Ensure sha256 was updated
        self.assertTrue(len(records[0].sha256) > 0)


class TestValuationCostBasisIntegration(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)
        self.db_path = str(self.test_dir / "test.db")
        self.cache_dir = str(self.test_dir / "prices")
        os.makedirs(self.cache_dir, exist_ok=True)

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_valuation_computes_net_unrealized_gain(self):
        # Create minimal price cache
        daily_prices = {
            "date": "2026-09-22",
            "prices": {
                "101": {
                    "Normal": {"marketPrice": 200.00},
                }
            }
        }
        with open(os.path.join(self.cache_dir, "2026-09-22.json"), "w", encoding="utf-8") as f:
            json.dump(daily_prices, f)

        # Create cards.json catalog
        cards_catalog = {
            "101": {
                "productId": 101,
                "name": "Judy Alvarez",
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "001",
                "rarity": "Secret",
            }
        }
        with open(os.path.join(self.cache_dir, "cards.json"), "w", encoding="utf-8") as f:
            json.dump(cards_catalog, f)

        # Create collection CSV (market value = 2 * 200.00 = 400.00)
        collection_csv = self.test_dir / "collection.csv"
        collection_csv.write_text(
            "name,expansion,printNumber,finish,totalQtyOwned\n"
            "Judy Alvarez,Welcome to Night City - Beta,001,Standard,2\n",
            encoding="utf-8"
        )

        res = calculate_portfolio_valuation(
            date_str="2026-09-22",
            collection_path=str(collection_csv),
            cache_dir=self.cache_dir,
            db_path=self.db_path,
            total_cost_basis=250.00,
            force=True,
        )

        self.assertEqual(res["total_value"], 400.00)
        self.assertEqual(res["total_cost_basis"], 250.00)
        # Net gain = 400.00 - 250.00 = 150.00
        self.assertEqual(res["net_unrealized_gain"], 150.00)
        # Net pct = (150.00 / 250.00) * 100 = 60.0%
        self.assertEqual(res["net_unrealized_pct"], 60.00)


if __name__ == "__main__":
    unittest.main()
