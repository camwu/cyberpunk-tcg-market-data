"""
Unit tests for purchase history management, SHA-256 caching, and cost basis tracking.
Uses isolated temporary directories and synthetic mock fixtures only.
"""

import csv
import json
import os
from pathlib import Path
import io
import tempfile
import time
import unittest
from unittest.mock import patch
import warnings

import tracker.purchases
from tracker.purchases import (
    PurchaseRecord,
    compute_file_sha256,
    extract_text_from_document,
    get_purchase_history_updated_at,
    parse_date_candidate,
    validate_amount_string,
    prompt_for_amount,
    parse_receipt_document,
    load_purchase_ledger,
    save_purchase_ledger,
    load_purchase_cache,
    save_purchase_cache,
    sync_purchase_history,
    validate_manual_record,
    validate_date_string,
    build_manual_sentinel_payload,
)
from tracker.validation import CollectionValidationError
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

    def test_validate_amount_string(self):
        # Valid amounts
        self.assertEqual(validate_amount_string("10"), 10.0)
        self.assertEqual(validate_amount_string("10.00"), 10.0)
        self.assertEqual(validate_amount_string("43.90"), 43.9)
        self.assertEqual(validate_amount_string("$10"), 10.0)
        self.assertEqual(validate_amount_string("$43.90"), 43.9)
        self.assertEqual(validate_amount_string("  $10.50  "), 10.5)

        # Invalid amounts
        self.assertIsNone(validate_amount_string("-1"))
        self.assertIsNone(validate_amount_string("-10.00"))
        self.assertIsNone(validate_amount_string("3.001"))
        self.assertIsNone(validate_amount_string("3 . 01"))
        self.assertIsNone(validate_amount_string("abc"))
        self.assertIsNone(validate_amount_string(""))
        self.assertIsNone(validate_amount_string("0"))
        self.assertIsNone(validate_amount_string("0.00"))
        self.assertIsNone(validate_amount_string("10.5"))
        self.assertIsNone(validate_amount_string(None))

    def test_parse_receipt_document_labeled_total(self):
        receipt_file = self.purchase_dir / "kickstarter_order.txt"
        receipt_file.write_text(
            "Kickstarter Pledge Confirmed\n"
            "Date: 2026-04-17\n"
            "Backer #100\n"
            "Pledge total: $349.00\n",
            encoding="utf-8"
        )
        parsed = parse_receipt_document(str(receipt_file))
        self.assertIsNotNone(parsed)
        p_date, p_merchant, p_amount, p_desc = parsed
        self.assertEqual(p_date, "2026-04-17")
        self.assertEqual(p_merchant, "Kickstarter")
        self.assertEqual(p_amount, 349.00)

    def test_parse_receipt_document_single_unlabeled_amount(self):
        receipt_file = self.purchase_dir / "2026-09-18_GooglePay.txt"
        receipt_file.write_text(
            "Google Pay\n"
            "Paper Hero's Games\n"
            "Sep 18, 2026 at 3:57 PM\n"
            "Total charged: None\n"
            "$43.90\n"
            "Paid with Visa\n",
            encoding="utf-8"
        )
        parsed = parse_receipt_document(str(receipt_file))
        self.assertIsNotNone(parsed)
        p_date, p_merchant, p_amount, p_desc = parsed
        self.assertEqual(p_date, "2026-09-18")
        self.assertEqual(p_merchant, "Paper Hero's Games")
        self.assertEqual(p_amount, 43.90)

    def test_prompt_for_amount_interactive_candidate_selection(self):
        with patch("builtins.input", return_value="2"):
            amt = prompt_for_amount("doc.pdf", [10.0, 50.0], interactive=True)
            self.assertEqual(amt, 50.0)

    def test_prompt_for_amount_interactive_custom_amount(self):
        with patch("builtins.input", side_effect=["invalid", "43.90"]):
            amt = prompt_for_amount("doc.pdf", [], interactive=True)
            self.assertEqual(amt, 43.90)

    def test_prompt_for_amount_interactive_skip(self):
        with patch("builtins.input", return_value="s"):
            amt = prompt_for_amount("doc.pdf", [10.0], interactive=True)
            self.assertIsNone(amt)

    def test_prompt_for_amount_non_interactive(self):
        # Multiple candidates: bottom-most
        self.assertEqual(prompt_for_amount("doc.pdf", [10.0, 43.90], interactive=False), 43.90)
        # Zero candidates: None
        self.assertIsNone(prompt_for_amount("doc.pdf", [], interactive=False))

    def test_parse_receipt_document_unparseable_skips(self):
        empty_file = self.purchase_dir / "empty.txt"
        empty_file.write_text("", encoding="utf-8")
        self.assertIsNone(parse_receipt_document(str(empty_file)))

        no_date_file = self.purchase_dir / "nodate.txt"
        no_date_file.write_text("Just some text without any date: $10.00", encoding="utf-8")
        self.assertIsNone(parse_receipt_document(str(no_date_file)))

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
        file1 = self.purchase_dir / "kickstarter_receipt.txt"
        file1.write_text("Kickstarter\nDate: 2026-04-17\nTotal: $349.00\n", encoding="utf-8")

        file2 = self.purchase_dir / "ebay_receipt.txt"
        file2.write_text("eBay order\nDate: 2026-09-13\nOrder Total: $11.52\n", encoding="utf-8")

        # Initial sync: should parse and create cache + ledger
        total, records, changed = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
            interactive=False,
        )
        self.assertEqual(total, 360.52)
        self.assertEqual(len(records), 2)
        self.assertTrue(changed)
        self.assertTrue(os.path.isfile(self.cache_path))
        self.assertTrue(os.path.isfile(self.ledger_path))

        # Second sync: cache hit, zero re-parsing needed
        cache_data_before = load_purchase_cache(self.cache_path)
        total2, records2, changed2 = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
            interactive=False,
        )
        self.assertEqual(total2, 360.52)
        self.assertEqual(len(records2), 2)
        self.assertFalse(changed2)
        cache_data_after = load_purchase_cache(self.cache_path)
        self.assertEqual(cache_data_before["files"], cache_data_after["files"])

    def test_sync_purchase_history_ledger_override(self):
        file1 = self.purchase_dir / "2026-09-18_Event_Entry.txt"
        file1.write_text("Paper Hero's Games\nDate: 2026-09-18\n", encoding="utf-8")

        # Pre-seed ledger with manual user entry
        initial_rec = PurchaseRecord(
            date="2026-09-18",
            merchant="Paper Hero's Games",
            amount=43.90,
            description="Beta Event Entry",
            filename="2026-09-18_Event_Entry.txt",
            sha256="",
        )
        save_purchase_ledger(self.ledger_path, [initial_rec])

        total, records, changed = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
            interactive=False,
        )
        self.assertEqual(total, 43.90)
        self.assertEqual(len(records), 1)
        self.assertTrue(changed)
        self.assertEqual(records[0].amount, 43.90)
        self.assertEqual(records[0].merchant, "Paper Hero's Games")
        # Ensure sha256 was updated
        self.assertTrue(len(records[0].sha256) > 0)

    def test_sync_purchase_history_zero_reliance_on_filenames(self):
        # Filename has arbitrary non-date, non-amount name
        arbitrary_file = self.purchase_dir / "receipt_random_payload.txt"
        arbitrary_file.write_text(
            "TCGplayer order\n"
            "Date: 2026-09-11\n"
            "Blue Yellow lot\n"
            "Amount Paid: $32.78\n",
            encoding="utf-8"
        )

        total, records, changed = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
            interactive=False,
        )
        self.assertEqual(total, 32.78)
        self.assertEqual(len(records), 1)
        self.assertTrue(changed)
        self.assertEqual(records[0].date, "2026-09-11")
        self.assertEqual(records[0].amount, 32.78)
        self.assertEqual(records[0].merchant, "TCGplayer")

    def test_sync_purchase_history_reparse_flag(self):
        f = self.purchase_dir / "receipt.txt"
        f.write_text(
            "TCGplayer\n"
            "Date: 2026-09-11\n"
            "Amount Paid: $32.78\n",
            encoding="utf-8"
        )

        # Initial sync creates cache and ledger
        total, records, changed = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
            interactive=False,
        )
        self.assertEqual(total, 32.78)
        self.assertTrue(changed)

        # User customizes the description in the CSV ledger
        ledger_records = load_purchase_ledger(self.ledger_path)
        ledger_records["receipt.txt"].description = "Custom User Description"
        save_purchase_ledger(self.ledger_path, list(ledger_records.values()))

        # Reparse with reparse=True
        total_reparsed, records_reparsed, changed_reparsed = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
            interactive=False,
            reparse=True,
        )
        self.assertEqual(total_reparsed, 32.78)
        self.assertEqual(len(records_reparsed), 1)
        self.assertTrue(changed_reparsed)
        # Custom description preserved
        self.assertEqual(records_reparsed[0].description, "Custom User Description")
        self.assertEqual(records_reparsed[0].amount, 32.78)

    def test_sync_purchase_history_no_rewrite_when_unchanged(self):
        file1 = self.purchase_dir / "receipt1.txt"
        file1.write_text("Kickstarter\nDate: 2026-04-17\nTotal: $100.00\n", encoding="utf-8")

        # Initial sync creates cache and ledger
        total, records, changed = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
            interactive=False,
        )
        self.assertEqual(total, 100.00)
        self.assertEqual(len(records), 1)
        self.assertTrue(changed)

        past_time = time.time() - 100.0
        os.utime(self.cache_path, (past_time, past_time))
        os.utime(self.ledger_path, (past_time, past_time))
        mtime_cache_1 = os.path.getmtime(self.cache_path)
        mtime_ledger_1 = os.path.getmtime(self.ledger_path)
        last_updated_1 = load_purchase_cache(self.cache_path)["last_updated"]

        # Second sync with zero modifications
        total2, records2, changed2 = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
            interactive=False,
        )
        self.assertEqual(total2, 100.00)
        self.assertEqual(len(records2), 1)
        self.assertFalse(changed2)
        # Ensure files were NOT rewritten on disk
        self.assertEqual(os.path.getmtime(self.cache_path), mtime_cache_1)
        self.assertEqual(os.path.getmtime(self.ledger_path), mtime_ledger_1)
        self.assertEqual(load_purchase_cache(self.cache_path)["last_updated"], last_updated_1)

        # Adding a new file triggers an update
        file2 = self.purchase_dir / "receipt2.txt"
        file2.write_text("eBay\nDate: 2026-09-13\nOrder Total: $50.00\n", encoding="utf-8")
        total3, records3, changed3 = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
            interactive=False,
        )
        self.assertEqual(total3, 150.00)
        self.assertEqual(len(records3), 2)
        self.assertTrue(changed3)
        # Ensure files were rewritten on disk
        self.assertGreater(os.path.getmtime(self.cache_path), mtime_cache_1)
        self.assertGreater(os.path.getmtime(self.ledger_path), mtime_ledger_1)

    def test_extract_text_missing_pypdf_warning_deduplicated(self):
        fake_pdf = self.test_dir / "sample.pdf"
        fake_pdf.write_text("Dummy binary content", encoding="utf-8")
        with patch("tracker.purchases.pypdf", None), warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always")
            res1 = extract_text_from_document(str(fake_pdf))
            self.assertEqual(res1, "")
            user_warnings = [item for item in recorded if issubclass(item.category, UserWarning) and "pypdf" in str(item.message)]
            self.assertGreaterEqual(len(user_warnings), 1)
            self.assertEqual(user_warnings[0].category, UserWarning)
            self.assertIn("pypdf", str(user_warnings[0].message))

    def test_get_purchase_history_updated_at_formatting(self):
        self.assertIsNone(get_purchase_history_updated_at(None))
        self.assertIsNone(get_purchase_history_updated_at(str(self.test_dir / "nonexistent.json")))

        # Test standard datetime string
        cache_file = self.test_dir / "test_cache.json"
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"last_updated": "2026-09-25 21:15:32"}, f)
        formatted = get_purchase_history_updated_at(str(cache_file))
        self.assertEqual(formatted, "2026-09-25 09:15 PM")

        # Test ISO format with T
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump({"last_updated": "2026-09-25T14:30:00"}, f)
        formatted_iso = get_purchase_history_updated_at(str(cache_file))
        self.assertEqual(formatted_iso, "2026-09-25 02:30 PM")

    def test_validate_manual_record_valid_and_defaults(self):
        # Explicit fields
        d, m, a, desc = validate_manual_record("2026-09-15", "35.00", "Local Game Store", "3x packs")
        self.assertEqual(d, "2026-09-15")
        self.assertEqual(m, "Local Game Store")
        self.assertEqual(a, 35.00)
        self.assertEqual(desc, "3x packs")

        # Defaults for merchant and description
        d2, m2, a2, desc2 = validate_manual_record("2026-09-15", 50, None, None)
        self.assertEqual(d2, "2026-09-15")
        self.assertEqual(m2, "Manual Purchase")
        self.assertEqual(a2, 50.00)
        self.assertEqual(desc2, "Manual Purchase Purchase (2026-09-15)")

    def test_validate_date_string(self):
        self.assertEqual(validate_date_string("2026-09-15"), "2026-09-15")
        self.assertIsNone(validate_date_string("Sep 15, 2026"))
        self.assertIsNone(validate_date_string("invalid-date"))
        self.assertIsNone(validate_date_string("09-15-2026"))
        self.assertIsNone(validate_date_string("2026-9-15"))
        self.assertIsNone(validate_date_string("2026-02-31"))
        self.assertIsNone(validate_date_string(None))
        self.assertIsNone(validate_date_string(12345))

    def test_validate_manual_record_invalid(self):
        # Invalid dates
        with self.assertRaises(ValueError):
            validate_manual_record("invalid-date", "35.00", "Store")
        with self.assertRaises(ValueError):
            validate_manual_record("2026-02-31", "35.00", "Store")
        with self.assertRaises(ValueError):
            validate_manual_record("Sep 15, 2026", "35.00", "Store")

        # Invalid amounts
        with self.assertRaises(ValueError):
            validate_manual_record("2026-09-15", "0.00", "Store")
        with self.assertRaises(ValueError):
            validate_manual_record("2026-09-15", "-10.00", "Store")
        with self.assertRaises(ValueError):
            validate_manual_record("2026-09-15", "not_a_number", "Store")

    def test_parse_receipt_document_cash_sentinel_valid(self):
        cash_file = self.purchase_dir / "cash_2026-09-15_local_game_store.json"
        cash_file.write_text(
            json.dumps({
                "date": "2026-09-15",
                "amount": 35.00,
                "merchant": "Local Game Store",
                "description": "Cash trade, 3x booster packs",
                "cash": True,
            }),
            encoding="utf-8",
        )

        parsed = parse_receipt_document(str(cash_file))
        self.assertIsNotNone(parsed)
        p_date, p_merchant, p_amount, p_desc = parsed
        self.assertEqual(p_date, "2026-09-15")
        self.assertEqual(p_merchant, "Local Game Store")
        self.assertEqual(p_amount, 35.00)
        self.assertEqual(p_desc, "Cash trade, 3x booster packs")

    def test_parse_receipt_document_cash_sentinel_fail_closed(self):
        # Corrupt data inside cash sentinel raises CollectionValidationError
        corrupt_data_file = self.purchase_dir / "cash_corrupt_data.json"
        corrupt_data_file.write_text(
            json.dumps({
                "date": "invalid-date",
                "amount": 35.00,
                "merchant": "Store",
                "cash": True,
            }),
            encoding="utf-8",
        )
        with self.assertRaises(CollectionValidationError):
            parse_receipt_document(str(corrupt_data_file))

        # Invalid JSON syntax falls through to standard parser (returns None, skipped with warning)
        # — only structurally valid cash: true sentinels with bad field values raise CollectionValidationError
        corrupt_syntax_file = self.purchase_dir / "cash_corrupt_syntax.json"
        corrupt_syntax_file.write_text('{"cash": true, "amount": 35.00, invalid_json', encoding="utf-8")
        result = parse_receipt_document(str(corrupt_syntax_file))
        self.assertIsNone(result)

        # Non-sentinel .json files with corrupt syntax also fall through silently
        corrupt_generic_json = self.purchase_dir / "bad_generic.json"
        corrupt_generic_json.write_text('{not valid json at all', encoding="utf-8")
        result2 = parse_receipt_document(str(corrupt_generic_json))
        self.assertIsNone(result2)

    def test_sync_purchase_history_cash_durability_and_caching(self):
        cash_file = self.purchase_dir / "cash_2026-09-15_local_game_store.json"
        cash_file.write_text(
            json.dumps({
                "date": "2026-09-15",
                "amount": 35.00,
                "merchant": "Local Game Store",
                "description": "Cash purchase",
                "cash": True,
            }),
            encoding="utf-8",
        )

        # Initial sync: should parse and record
        total, records, changed = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
            interactive=False,
        )
        self.assertEqual(total, 35.00)
        self.assertEqual(len(records), 1)
        self.assertTrue(changed)
        self.assertEqual(records[0].amount, 35.00)
        self.assertEqual(records[0].merchant, "Local Game Store")

        # Second sync: cache hit, zero re-parsing needed
        total2, records2, changed2 = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
            interactive=False,
        )
        self.assertEqual(total2, 35.00)
        self.assertEqual(len(records2), 1)
        self.assertFalse(changed2)

        # Third sync with reparse=True: cash entry is completely preserved
        total3, records3, changed3 = sync_purchase_history(
            purchase_dir=str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
            reparse=True,
            interactive=False,
        )
        self.assertEqual(total3, 35.00)
        self.assertEqual(len(records3), 1)
        self.assertTrue(changed3)
        self.assertEqual(records3[0].amount, 35.00)

    def test_build_manual_sentinel_payload_amount_formatting(self):
        # Whole-dollar amounts serialize as integers
        p10 = build_manual_sentinel_payload("2026-10-04", "Store", 10.0, "Items")
        self.assertEqual(p10["amount"], 10)
        self.assertIsInstance(p10["amount"], int)
        self.assertTrue(p10["manual"])
        self.assertNotIn("method", p10)

        p35 = build_manual_sentinel_payload("2026-09-15", "Store", 35.0, "Items")
        self.assertEqual(p35["amount"], 35)
        self.assertIsInstance(p35["amount"], int)
        self.assertTrue(p35["manual"])
        self.assertNotIn("method", p35)

        # Fractional-dollar amounts preserve float decimals
        p12 = build_manual_sentinel_payload("2026-10-04", "Store", 12.55, "Items")
        self.assertEqual(p12["amount"], 12.55)
        self.assertIsInstance(p12["amount"], float)
        self.assertTrue(p12["manual"])
        self.assertNotIn("method", p12)

        # JSON text formatting check
        s10 = json.dumps(p10)
        self.assertIn('"amount": 10', s10)
        self.assertNotIn('"amount": 10.0', s10)

        s12 = json.dumps(p12)
        self.assertIn('"amount": 12.55', s12)

    def test_manual_sentinel_integer_round_trip(self):
        payload = build_manual_sentinel_payload("2026-09-15", "Local Game Store", 35.0, "3x packs")
        sentinel_file = self.purchase_dir / "2026-09-15_local_game_store_roundtrip_manual.json"
        with open(sentinel_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

        raw_text = sentinel_file.read_text(encoding="utf-8")
        self.assertIn('"amount": 35', raw_text)
        self.assertNotIn('"amount": 35.0', raw_text)

        parsed = parse_receipt_document(str(sentinel_file))
        self.assertIsNotNone(parsed)
        p_date, p_merchant, p_amount, p_desc = parsed
        self.assertEqual(p_amount, 35.0)
        self.assertIsInstance(p_amount, float)

    def test_parse_receipt_document_manual_sentinel_valid(self):
        manual_file = self.purchase_dir / "2026-09-15_local_game_store_manual.json"
        manual_file.write_text(
            json.dumps({
                "date": "2026-09-15",
                "amount": 42.50,
                "merchant": "Local Game Store",
                "description": "Manual payment for singles",
                "manual": True,
            }),
            encoding="utf-8",
        )
        parsed = parse_receipt_document(str(manual_file))
        self.assertIsNotNone(parsed)
        d, m, a, desc = parsed
        self.assertEqual(d, "2026-09-15")
        self.assertEqual(m, "Local Game Store")
        self.assertEqual(a, 42.50)
        self.assertEqual(desc, "Manual payment for singles")

    def test_parse_receipt_document_manual_sentinel_corrupt_fails(self):
        # Invalid date
        invalid_date_file = self.purchase_dir / "invalid_date_manual.json"
        invalid_date_file.write_text(
            json.dumps({
                "date": "invalid-date",
                "amount": 20.00,
                "merchant": "Store",
                "manual": True,
            }),
            encoding="utf-8",
        )
        with self.assertRaises(CollectionValidationError):
            parse_receipt_document(str(invalid_date_file))

        # Invalid amount
        invalid_amt_file = self.purchase_dir / "invalid_amt_manual.json"
        invalid_amt_file.write_text(
            json.dumps({
                "date": "2026-09-15",
                "amount": -5.00,
                "merchant": "Store",
                "manual": True,
            }),
            encoding="utf-8",
        )
        with self.assertRaises(CollectionValidationError):
            parse_receipt_document(str(invalid_amt_file))

    def test_parse_receipt_document_sentinel_precedence(self):
        # Both manual: true and cash: true present
        precedence_file = self.purchase_dir / "precedence_manual.json"
        precedence_file.write_text(
            json.dumps({
                "date": "2026-09-15",
                "amount": 55.00,
                "merchant": "Store",
                "manual": True,
                "cash": True,
            }),
            encoding="utf-8",
        )
        parsed = parse_receipt_document(str(precedence_file))
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[2], 55.00)

        # Corrupt sentinel fails
        corrupt_file = self.purchase_dir / "corrupt_precedence_manual.json"
        corrupt_file.write_text(
            json.dumps({
                "date": "2026-09-15",
                "amount": 0,
                "merchant": "Store",
                "manual": True,
                "cash": True,
            }),
            encoding="utf-8",
        )
        with self.assertRaises(CollectionValidationError):
            parse_receipt_document(str(corrupt_file))

    def test_sync_purchase_history_manual_durability(self):
        manual_file = self.purchase_dir / "2026-09-15_store_singles_manual.json"
        manual_file.write_text(
            json.dumps({
                "date": "2026-09-15",
                "amount": 75.00,
                "merchant": "Game Shop",
                "description": "Booster Box",
                "manual": True,
                "method": "card",
            }),
            encoding="utf-8",
        )
        total, records, changed = sync_purchase_history(
            str(self.purchase_dir),
            reparse=False,
            interactive=False,
        )
        self.assertEqual(total, 75.00)
        self.assertEqual(len(records), 1)
        self.assertTrue(changed)
        self.assertEqual(records[0].merchant, "Game Shop")

        # Reparse retains sentinel
        total2, records2, changed2 = sync_purchase_history(
            str(self.purchase_dir),
            reparse=True,
            interactive=False,
        )
        self.assertEqual(total2, 75.00)
        self.assertEqual(len(records2), 1)

    def test_parse_receipt_shopify_format_with_usd_total(self):
        """Verifies Total USD$44.10 extracts 44.10 without picking up Subtotal $40.00."""
        text = (
            "Cyberpunk pre release 1 $40.00\n"
            "Subtotal $40.00\n"
            "Taxes $4.10\n"
            "Total USD$44.10\n"
            "Confirmed Oct 31\n"
            "Order #6631 - Example Card Shop - Account\n"
            "11/9/26, 3:31 PM\n"
        )
        p = self.purchase_dir / "receipt.txt"
        p.write_text(text, encoding="utf-8")
        with patch("builtins.input", return_value=""):
            res = parse_receipt_document(str(p), interactive=True)
        self.assertIsNotNone(res)
        date, merchant, amount, desc = res
        self.assertEqual(amount, 44.10)
        self.assertEqual(merchant, "Example Card Shop")
        self.assertEqual(date, "2026-10-31")

    def test_parse_receipt_web_order_merchant(self):
        """Verifies generic web order title and sold-by merchant recognition."""
        text_and = "Order #6935\nConfirmed Nov 7\nOrders sold by City Brawl Games\nTotal USD $10.00\n11/9/26, 3:31 PM\n"
        p1 = self.purchase_dir / "and.txt"
        p1.write_text(text_and, encoding="utf-8")
        with patch("builtins.input", return_value=""):
            res1 = parse_receipt_document(str(p1), interactive=True)
        self.assertIsNotNone(res1)
        self.assertEqual(res1[1], "City Brawl Games")

        text_title = "Order #6822\nConfirmed Nov 5\nOrder #6822 - Hobby Haven - Account\nTotal $10.00\n11/9/26, 3:31 PM\n"
        p2 = self.purchase_dir / "amp.txt"
        p2.write_text(text_title, encoding="utf-8")
        with patch("builtins.input", return_value=""):
            res2 = parse_receipt_document(str(p2), interactive=True)
        self.assertIsNotNone(res2)
        self.assertEqual(res2[1], "Hobby Haven")

        # Hyphenated merchant title
        text_hyphen = "Order #6631\nConfirmed Nov 5\nOrder #6631 - Sci-Fi City - Account\nTotal $10.00\n11/9/26, 3:31 PM\n"
        p3 = self.purchase_dir / "hyphen.txt"
        p3.write_text(text_hyphen, encoding="utf-8")
        with patch("builtins.input", return_value=""):
            res3 = parse_receipt_document(str(p3), interactive=True)
        self.assertIsNotNone(res3)
        self.assertEqual(res3[1], "Sci-Fi City")

    def test_parse_receipt_year_rollover_from_future_print_timestamp(self):
        """Verifies that late-year orders printed in January default to the prior year."""
        text_rollover = (
            "Confirmed Dec 28\n"
            "Order #7100 - Sample Games - Account\n"
            "Total USD$25.00\n"
            "1/3/27, 10:00 AM\n"
        )
        p = self.purchase_dir / "rollover.txt"
        p.write_text(text_rollover, encoding="utf-8")
        # Empty string accepts default year, which should be 2026 (not 2027)
        with patch("builtins.input", return_value=""):
            res = parse_receipt_document(str(p), interactive=True)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "2026-12-28")
        self.assertEqual(res[1], "Sample Games")
        self.assertEqual(res[2], 25.00)

    def test_parse_receipt_anchored_date_avoids_false_positives(self):
        """Verifies street addresses or shipping estimates are not mistaken for transaction dates."""
        text_address = (
            "Billing Address: 123 May 12th St, Suite 4\n"
            "Shipping window: Arrives Nov 10 by 8 PM\n"
            "Item: March of the Machines\n"
            "Confirmed Oct 31\n"
            "Total USD$44.10\n"
            "Order #6631 - Sample Games - Account\n"
            "11/9/26, 3:31 PM\n"
        )
        p = self.purchase_dir / "addr.txt"
        p.write_text(text_address, encoding="utf-8")
        with patch("builtins.input", return_value=""):
            res = parse_receipt_document(str(p), interactive=True)
        self.assertIsNotNone(res)
        self.assertEqual(res[0], "2026-10-31")

    def test_prompt_for_year_interactive_and_non_interactive(self):
        """Verifies interactive year resolution with default and explicit input, and fail-closed non-interactive."""
        from tracker.purchases import prompt_for_year

        # 1. Non-interactive -> None
        self.assertIsNone(
            prompt_for_year("test.pdf", "10", "31", "Oct", default_year=2026, interactive=False)
        )

        # 2. Interactive with empty string (accepts default)
        with patch("builtins.input", return_value=""):
            res_default = prompt_for_year("test.pdf", "10", "31", "Oct", default_year=2026, interactive=True)
            self.assertEqual(res_default, "2026-10-31")

        # 3. Interactive with explicit year
        with patch("builtins.input", return_value="2025"):
            res_explicit = prompt_for_year("test.pdf", "10", "31", "Oct", default_year=2026, interactive=True)
            self.assertEqual(res_explicit, "2025-10-31")

        # 4. Interactive skip option
        with patch("builtins.input", return_value="s"):
            res_skip = prompt_for_year("test.pdf", "10", "31", "Oct", default_year=2026, interactive=True)
            self.assertIsNone(res_skip)

    def test_sync_purchase_history_content_addressing_on_rename(self):
        """Verifies that renamed files with identical SHA-256 are recognized from cache without prompting."""
        p1 = self.purchase_dir / "orig.txt"
        text = (
            "Confirmed Oct 31\n"
            "In-store purchase\n"
            "Order #6631 - Sample Games - Account\n"
            "Total USD$44.10\n"
            "11/9/26, 3:31 PM\n"
        )
        p1.write_text(text, encoding="utf-8")

        # Initial ingestion with interactive prompt
        with patch("builtins.input", return_value=""):
            tot, recs, updated = sync_purchase_history(
                str(self.purchase_dir),
                cache_path=self.cache_path,
                ledger_path=self.ledger_path,
                interactive=True,
            )
        self.assertEqual(tot, 44.10)
        self.assertTrue(updated)
        self.assertEqual(len(recs), 1)

        # Rename file on disk
        p1.unlink()
        p2 = self.purchase_dir / "renamed_receipt.txt"
        p2.write_text(text, encoding="utf-8")

        # Second sync in non-interactive mode should hit SHA-256 content cache without prompting
        tot2, recs2, updated2 = sync_purchase_history(
            str(self.purchase_dir),
            cache_path=self.cache_path,
            ledger_path=self.ledger_path,
            interactive=False,
        )
        self.assertEqual(tot2, 44.10)
        self.assertEqual(len(recs2), 1)
        self.assertEqual(recs2[0].date, "2026-10-31")
        self.assertEqual(recs2[0].amount, 44.10)
        self.assertEqual(recs2[0].filename, "renamed_receipt.txt")

    def test_parse_actual_web_receipt_structures(self):
        """Verifies synthetic text matching web receipt layouts with generic titles."""
        samples = [
            (
                "2026-10-31_sample.txt",
                (
                    "C\nOrders Profile\nConfirmed Oct 31\nOrder #6631\nIn-store purchase\n"
                    "Oct 31 at 12:08 PM\nCyberpunk pre release\n1\n$40.00\nSubtotal $40.00\n"
                    "Taxes $4.10\nTotal USD$44.10\nBilling address -\nPayment Visa 7127\n"
                    "11/9/26, 3:31 PM Order #6631 - Sample Shop - Account\n"
                ),
                ("2026-10-31", "Sample Shop", 44.10),
            ),
            (
                "2026-11-05_sample.txt",
                (
                    "C\nOrders Profile\nConfirmed Nov 5\nOrder #6822\nIn-store purchase\n"
                    "Nov 5 at 6:48 PM\nCyberpunk weekly\n1\n$10.00\nSubtotal $10.00\n"
                    "Total USD$10.00\nBilling address -\nPayment Visa 7127\n"
                    "11/9/26, 3:31 PM Order #6822 - Sample Shop - Account\n"
                ),
                ("2026-11-05", "Sample Shop", 10.00),
            ),
            (
                "2026-11-07_sample.txt",
                (
                    "C\nOrders Profile\nConfirmed Nov 7\nOrder #6935\nIn-store purchase\n"
                    "Nov 7 at 2:18 PM\nCyberpunk weekly\n1\n$10.00\nSubtotal $10.00\n"
                    "Total USD$10.00\nBilling address -\nPayment Visa 7127\nOrders sold by Sample Shop\n"
                    "11/9/26, 3:31 PM Order #6935 - Sample Shop - Account\n"
                ),
                ("2026-11-07", "Sample Shop", 10.00),
            ),
        ]
        for fname, raw_text, (exp_date, exp_merchant, exp_amount) in samples:
            f = self.purchase_dir / fname
            f.write_text(raw_text, encoding="utf-8")
            with patch("builtins.input", return_value=""):
                res = parse_receipt_document(str(f), interactive=True)
            self.assertIsNotNone(res, f"Failed to parse {fname}")
            d, m, a, _ = res
            self.assertEqual(d, exp_date)
            self.assertEqual(m, exp_merchant)
            self.assertEqual(a, exp_amount)


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

