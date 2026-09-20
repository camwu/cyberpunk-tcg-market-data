"""
Automated unit tests for collection CSV schema and data integrity validation.
"""

import tempfile
import unittest
from pathlib import Path

from tracker.validation import (
    parse_multi_lot_notes,
    validate_collection_file,
    validate_sealed_file,
    format_validation_report,
)


class TestCollectionValidation(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_dir = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_csv(self, filename: str, content: str) -> str:
        file_path = self.test_dir / filename
        file_path.write_text(content.strip(), encoding="utf-8")
        return str(file_path)

    def test_valid_collection_csv(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price
V - Corporate Exile,Box Toppers,006,Standard,2,45.00
Jackie Welles,Promos,005,Foil,1,120.50
"""
        path = self._create_csv("valid.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["name"], "V - Corporate Exile")
        self.assertEqual(rows[0]["expansion"], "Box Toppers")
        self.assertEqual(rows[0]["printNumber"], "006")
        self.assertEqual(rows[0]["finish"], "Standard")
        self.assertEqual(rows[0]["totalQtyOwned"], 2)
        self.assertEqual(rows[0]["price"], 45.0)

        self.assertEqual(rows[1]["name"], "Jackie Welles")
        self.assertEqual(rows[1]["expansion"], "Promos")
        self.assertEqual(rows[1]["printNumber"], "005")
        self.assertEqual(rows[1]["finish"], "Foil")
        self.assertEqual(rows[1]["totalQtyOwned"], 1)
        self.assertEqual(rows[1]["price"], 120.5)

    def test_whitespace_in_headers(self):
        csv_content = """  name  , expansion , printNumber ,  finish  , totalQtyOwned , price 
V - Corporate Exile,Box Toppers,006,Standard,2,45.00
"""
        path = self._create_csv("whitespace_headers.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["name"], "V - Corporate Exile")
        self.assertEqual(rows[0]["expansion"], "Box Toppers")
        self.assertEqual(rows[0]["printNumber"], "006")
        self.assertEqual(rows[0]["finish"], "Standard")
        self.assertEqual(rows[0]["totalQtyOwned"], 2)
        self.assertEqual(rows[0]["price"], 45.0)

    def test_finish_normalization(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned
V,Promo,001,Normal,1
Jackie,Promo,002,foil,1
Viktor,Promo,003,standard,1
"""
        path = self._create_csv("normalize.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)
        self.assertEqual(rows[0]["finish"], "Standard")
        self.assertEqual(rows[1]["finish"], "Foil")
        self.assertEqual(rows[2]["finish"], "Standard")

    def test_invalid_finish(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned
V,Promo,001,Matte,1
Jackie,Promo,002,,1
"""
        path = self._create_csv("invalid_finish.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertFalse(is_valid)
        self.assertEqual(len(rows), 0)
        self.assertTrue(any("must be 'Standard' or 'Foil'" in err for err in errors))
        self.assertTrue(any("'finish' is empty" in err for err in errors))

    def test_missing_required_columns(self):
        csv_content = """name,printNumber,finish,totalQtyOwned
V,001,Standard,1
"""
        path = self._create_csv("missing_cols.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertFalse(is_valid)
        self.assertEqual(len(rows), 0)
        self.assertTrue(any("Missing required columns" in err and "expansion" in err for err in errors))

    def test_invalid_quantity(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned
V,Promo,001,Standard,0
Jackie,Promo,002,Standard,-3
Viktor,Promo,003,Standard,many
"""
        path = self._create_csv("invalid_qty.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertFalse(is_valid)
        self.assertEqual(len(rows), 0)
        self.assertTrue(any("must be at least 1" in err for err in errors))
        self.assertTrue(any("must be an integer" in err for err in errors))

    def test_invalid_price(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price
V,Promo,001,Standard,1,-10.0
Jackie,Promo,002,Standard,1,expensive
"""
        path = self._create_csv("invalid_price.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertFalse(is_valid)
        self.assertEqual(len(rows), 0)
        self.assertTrue(any("must be non-negative" in err for err in errors))
        self.assertTrue(any("must be a valid number" in err for err in errors))

    def test_empty_and_nonexistent_file(self):
        empty_path = self._create_csv("empty.csv", "")
        is_valid_empty, errors_empty, rows_empty = validate_collection_file(empty_path)
        self.assertFalse(is_valid_empty)
        self.assertTrue(any("empty or missing header" in err for err in errors_empty))

        is_valid_missing, errors_missing, rows_missing = validate_collection_file("nonexistent.csv")
        self.assertFalse(is_valid_missing)
        self.assertTrue(any("not found" in err for err in errors_missing))

        is_valid_dir, errors_dir, rows_dir = validate_collection_file(str(self.test_dir))
        self.assertFalse(is_valid_dir)
        self.assertTrue(any("found a directory" in err for err in errors_dir))

    def test_valid_sealed_csv(self):
        csv_content = """productId,name,expansion,totalQtyOwned,price,acquisitionDate
714346,Welcome to Night City - Beta Booster Box,Welcome to Night City - Beta,1,216.08,2026-09-11
"""
        path = self._create_csv("valid_sealed.csv", csv_content)
        is_valid, errors, rows = validate_sealed_file(path)

        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["productId"], 714346)
        self.assertEqual(rows[0]["name"], "Welcome to Night City - Beta Booster Box")
        self.assertEqual(rows[0]["expansion"], "Welcome to Night City - Beta")
        self.assertEqual(rows[0]["totalQtyOwned"], 1)
        self.assertEqual(rows[0]["price"], 216.08)
        self.assertEqual(rows[0]["acquisitionDate"], "2026-09-11")
        self.assertEqual(rows[0]["item_type"], "Sealed")

    def test_missing_sealed_columns(self):
        csv_content = """productId,name,expansion,totalQtyOwned,price
714346,Welcome to Night City - Beta Booster Box,Welcome to Night City - Beta,1,216.08
"""
        path = self._create_csv("missing_cols_sealed.csv", csv_content)
        is_valid, errors, rows = validate_sealed_file(path)

        self.assertFalse(is_valid)
        self.assertEqual(len(rows), 0)
        self.assertTrue(any("acquisitionDate" in err for err in errors))

    def test_invalid_sealed_fields(self):
        csv_content = """productId,name,expansion,totalQtyOwned,price,acquisitionDate
invalid_id,,Welcome to Night City,0,-5.00,not-a-date
"""
        path = self._create_csv("invalid_sealed.csv", csv_content)
        is_valid, errors, rows = validate_sealed_file(path)

        self.assertFalse(is_valid)
        self.assertTrue(any("must be an integer" in err for err in errors))
        self.assertTrue(any("'name' is empty" in err for err in errors))
        self.assertTrue(any("must be at least 1" in err for err in errors))
        self.assertTrue(any("must be non-negative" in err for err in errors))
        self.assertTrue(any("YYYY-MM-DD" in err for err in errors))

    def test_duplicate_sealed_lot_rejected(self):
        csv_content = """productId,name,expansion,totalQtyOwned,price,acquisitionDate
714346,Welcome to Night City - Beta Booster Box,Welcome to Night City - Beta,1,216.08,2026-09-11
714346,Welcome to Night City - Beta Booster Box,Welcome to Night City - Beta,1,220.00,2026-09-11
"""
        path = self._create_csv("duplicate_lot_sealed.csv", csv_content)
        is_valid, errors, rows = validate_sealed_file(path)

        self.assertFalse(is_valid)
        self.assertTrue(any("Duplicate sealed lot" in err for err in errors))

    def test_empty_sealed_csv_with_headers_is_valid(self):
        csv_content = """productId,name,expansion,totalQtyOwned,price,acquisitionDate\n"""
        path = self._create_csv("empty_sealed_headers.csv", csv_content)
        is_valid, errors, rows = validate_sealed_file(path)

        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(rows), 0)

    def test_unified_collection_with_sealed_product(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price,notes
MaxTac Suppression Team,Welcome to Night City - Beta,B050,Standard,1,0.20,2026-09-02
Welcome to Night City - Beta Booster Box,Welcome to Night City - Beta,,Standard,1,243.16,2026-09-02
"""
        path = self._create_csv("unified.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(rows), 2)

        # Card row
        self.assertEqual(rows[0]["name"], "MaxTac Suppression Team")
        self.assertEqual(rows[0]["item_type"], "Card")
        self.assertEqual(rows[0]["printNumber"], "B050")
        self.assertEqual(rows[0]["acquisitionDate"], "2026-09-02")

        # Sealed row with empty printNumber
        self.assertEqual(rows[1]["name"], "Welcome to Night City - Beta Booster Box")
        self.assertEqual(rows[1]["item_type"], "Sealed")
        self.assertIsNone(rows[1]["printNumber"])
        self.assertEqual(rows[1]["finish"], "Standard")
        self.assertEqual(rows[1]["rarity"], "Sealed")
        self.assertEqual(rows[1]["acquisitionDate"], "2026-09-02")

    def test_card_with_empty_print_number_rejected_in_unified(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price
Johnny Silverhand,Welcome to Night City - Beta,,Standard,1,10.00
"""
        path = self._create_csv("bad_card.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertFalse(is_valid)
        self.assertEqual(len(rows), 0)
        self.assertTrue(any("Row 2: 'printNumber' is empty" in err for err in errors))

    def test_duplicate_sealed_lot_in_unified_rejected(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price,notes
Welcome to Night City - Beta Booster Box,Welcome to Night City - Beta,,Standard,1,243.16,2026-09-02
Welcome to Night City - Beta Booster Box,Welcome to Night City - Beta,,Standard,1,250.00,2026-09-02
"""
        path = self._create_csv("dupe_sealed_unified.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertFalse(is_valid)
        self.assertTrue(any("Duplicate sealed lot" in err for err in errors))

    def test_parse_multi_lot_notes_standalone_date(self):
        lots, err = parse_multi_lot_notes("2026-09-02", 3)
        self.assertIsNone(err)
        self.assertEqual(lots, [("2026-09-02", 3)])

    def test_parse_multi_lot_notes_semicolon_and_comma(self):
        lots_semi, err_semi = parse_multi_lot_notes("2026-09-02: 1; 2026-09-18: 2", 3)
        self.assertIsNone(err_semi)
        self.assertEqual(lots_semi, [("2026-09-02", 1), ("2026-09-18", 2)])

        lots_comma, err_comma = parse_multi_lot_notes("2026-09-02: 1, 2026-09-18: 2", 3)
        self.assertIsNone(err_comma)
        self.assertEqual(lots_comma, [("2026-09-02", 1), ("2026-09-18", 2)])

    def test_parse_multi_lot_notes_implicit_single_quantity(self):
        # 1 copy on 2026-09-02 (implicit), 2 copies on 2026-09-18 (explicit)
        lots, err = parse_multi_lot_notes("2026-09-02; 2026-09-18: 2", 3)
        self.assertIsNone(err)
        self.assertEqual(lots, [("2026-09-02", 1), ("2026-09-18", 2)])

        # 1 copy on each date (both implicit)
        lots_both, err_both = parse_multi_lot_notes("2026-09-02; 2026-09-18", 2)
        self.assertIsNone(err_both)
        self.assertEqual(lots_both, [("2026-09-02", 1), ("2026-09-18", 1)])

    def test_parse_multi_lot_notes_empty_or_no_dates(self):
        lots_empty, err = parse_multi_lot_notes("", 2)
        self.assertIsNone(err)
        self.assertEqual(lots_empty, [(None, 2)])

        lots_none, err = parse_multi_lot_notes(None, 4)
        self.assertIsNone(err)
        self.assertEqual(lots_none, [(None, 4)])

        lots_text, err = parse_multi_lot_notes("No dates here, just personal note", 1)
        self.assertIsNone(err)
        self.assertEqual(lots_text, [(None, 1)])

    def test_parse_multi_lot_notes_sum_mismatch_error(self):
        lots, err = parse_multi_lot_notes("2026-09-02: 1; 2026-09-18: 2", 5)
        self.assertIsNotNone(err)
        self.assertEqual(lots, [("2026-09-02", 1), ("2026-09-18", 2)])
        self.assertEqual(sum(q for _, q in lots), 3)
        self.assertIn("Sum of parsed lots (3) does not equal totalQtyOwned (5)", err)

        lots_implicit, err_implicit = parse_multi_lot_notes("2026-09-02; 2026-09-18", 3)
        self.assertIsNotNone(err_implicit)
        self.assertEqual(lots_implicit, [("2026-09-02", 1), ("2026-09-18", 1)])
        self.assertEqual(sum(q for _, q in lots_implicit), 2)
        self.assertIn("Sum of parsed lots (2) does not equal totalQtyOwned (3)", err_implicit)

        lots_single, err_single = parse_multi_lot_notes("2026-09-02: 2", 5)
        self.assertIsNotNone(err_single)
        self.assertEqual(lots_single, [("2026-09-02", 2)])
        self.assertEqual(sum(q for _, q in lots_single), 2)
        self.assertIn("Sum of parsed lots (2) does not equal totalQtyOwned (5)", err_single)

    def test_parse_multi_lot_notes_non_positive_quantity_rejected(self):
        # Explicit zero quantity in multi-lot list
        lots_zero, err_zero = parse_multi_lot_notes("2026-09-02: 0; 2026-09-18: 2", 2)
        self.assertIsNotNone(err_zero)
        self.assertEqual(lots_zero, [])
        self.assertIn("Parsed lot quantity for '2026-09-02' must be at least 1 (got 0)", err_zero)

        # Explicit negative quantity in multi-lot list
        lots_neg, err_neg = parse_multi_lot_notes("2026-09-02: -1; 2026-09-18: 2", 1)
        self.assertIsNotNone(err_neg)
        self.assertEqual(lots_neg, [])
        self.assertIn("Parsed lot quantity for '2026-09-02' must be at least 1 (got -1)", err_neg)

        # Explicit zero quantity in standalone single date
        lots_single_zero, err_single_zero = parse_multi_lot_notes("2026-09-02: 0", 0)
        self.assertIsNotNone(err_single_zero)
        self.assertEqual(lots_single_zero, [])
        self.assertIn("Parsed lot quantity for '2026-09-02' must be at least 1 (got 0)", err_single_zero)

    def test_validate_collection_file_multi_lot_card_expansion(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price,notes
Towerfall,Welcome to Night City - Beta,B034,Standard,3,1.50,"2026-09-02: 1; 2026-09-18: 2"
"""
        path = self._create_csv("multi_lot_expansion.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(rows), 2)

        self.assertEqual(rows[0]["name"], "Towerfall")
        self.assertEqual(rows[0]["printNumber"], "B034")
        self.assertEqual(rows[0]["totalQtyOwned"], 1)
        self.assertEqual(rows[0]["acquisitionDate"], "2026-09-02")

        self.assertEqual(rows[1]["name"], "Towerfall")
        self.assertEqual(rows[1]["printNumber"], "B034")
        self.assertEqual(rows[1]["totalQtyOwned"], 2)
        self.assertEqual(rows[1]["acquisitionDate"], "2026-09-18")

    def test_validate_collection_file_multi_lot_mismatch_fails_validation(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price,notes
Towerfall,Welcome to Night City - Beta,B034,Standard,5,1.50,"2026-09-02: 1; 2026-09-18: 2"
"""
        path = self._create_csv("multi_lot_mismatch.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertFalse(is_valid)
        self.assertEqual(len(rows), 0)
        self.assertTrue(any("Quantity mismatch for 'Towerfall'" in err for err in errors))
        self.assertTrue(any("Sum of parsed lots (3) does not equal totalQtyOwned (5)" in err for err in errors))

    def test_duplicate_card_lot_in_unified_rejected(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price,notes
Towerfall,Welcome to Night City - Beta,B034,Standard,1,1.50,2026-09-02
Towerfall,Welcome to Night City - Beta,B034,Standard,2,1.50,2026-09-02
"""
        path = self._create_csv("dupe_card_lot.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertFalse(is_valid)
        self.assertTrue(any("Duplicate card lot" in err for err in errors))
    def test_validate_collection_file_non_date_notes_falls_back_to_acquisition_date_col(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price,notes,acquisitionDate
Towerfall,Welcome to Night City - Beta,B034,Standard,1,1.50,"Personal collection from local store",2026-09-05
"""
        path = self._create_csv("notes_fallback_acq_col.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertTrue(is_valid)
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["acquisitionDate"], "2026-09-05")

    def test_validate_collection_file_diagnostics_include_print_number_and_notes(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price,notes
Towerfall,Welcome to Night City - Beta,B034,Standard,5,1.50,"2026-09-02: 1; 2026-09-18: 2"
"""
        path = self._create_csv("diagnostics_test.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path, error_export_path=None)

        self.assertFalse(is_valid)
        self.assertTrue(any("(B034)" in err for err in errors))
        self.assertTrue(any("(notes: '2026-09-02: 1; 2026-09-18: 2')" in err for err in errors))

    def test_validate_collection_file_exports_errors_to_csv_and_cleans_up_on_pass(self):
        csv_fail = """name,expansion,printNumber,finish,totalQtyOwned,price,notes
Towerfall,Welcome to Night City - Beta,B034,Standard,5,1.50,"2026-09-02: 1; 2026-09-18: 2"
"""
        fail_path = self._create_csv("export_fail.csv", csv_fail)
        error_csv = str(self.test_dir / "test_errors.csv")

        is_valid, errors, rows = validate_collection_file(fail_path, error_export_path=error_csv)
        self.assertFalse(is_valid)
        self.assertTrue(Path(error_csv).exists())

        error_content = Path(error_csv).read_text(encoding="utf-8")
        self.assertIn("row,print_number,name,parsed,qty,notes", error_content)
        self.assertIn("2,B034,Towerfall,3,5", error_content)

        csv_pass = """name,expansion,printNumber,finish,totalQtyOwned,price,notes
Towerfall,Welcome to Night City - Beta,B034,Standard,3,1.50,"2026-09-02: 1; 2026-09-18: 2"
"""
        pass_path = self._create_csv("export_pass.csv", csv_pass)
        is_valid_pass, errors_pass, rows_pass = validate_collection_file(pass_path, error_export_path=error_csv)
        self.assertTrue(is_valid_pass)
        self.assertFalse(Path(error_csv).exists())

    def test_validate_collection_file_truncation_notice_with_total_count(self):
        lines = ["name,expansion,printNumber,finish,totalQtyOwned,price,notes"]
        for i in range(1, 31):
            lines.append(f"Card {i},Welcome to Night City - Beta,B{i:03d},Standard,5,1.00,\"2026-09-02: 1; 2026-09-18: 2\"")
        path = self._create_csv("overflow_test.csv", "\n".join(lines))
        error_csv = str(self.test_dir / "overflow_errors.csv")

        is_valid, errors, rows = validate_collection_file(path, max_row_errors=10, error_export_path=error_csv)
        self.assertFalse(is_valid)
        self.assertTrue(any("truncated 20 additional row errors; 30 total errors encountered across CSV" in err for err in errors))
        self.assertTrue(any(f"Full error report written to: {error_csv}" in err for err in errors))
        self.assertEqual(len([e for e in errors if e.startswith("Row ")]), 10)

    def test_format_validation_report_renders_table_and_summary(self):
        errors = [
            "Row 12: Quantity mismatch for 'Peace Offering' (B101). Sum of parsed lots (2) does not equal totalQtyOwned (5) (notes: '2026-09-19: 2').",
            "Row 13: Quantity mismatch for 'Delamain - Rideshare AI' (B111). Sum of parsed lots (2) does not equal totalQtyOwned (5) (notes: '2026-09-19: 2').",
        ]
        report = format_validation_report(errors, error_csv_path="data/validation_errors.csv")
        self.assertIn("| Row | Print Num | Name", report)
        self.assertIn("| 12  | B101      | Peace Offering", report)
        self.assertIn("| 13  | B111      | Delamain - Rideshare AI", report)
        self.assertIn("Validation found 2 total lot mismatches across the CSV.", report)
        self.assertIn("Full error report written to: data/validation_errors.csv", report)

    def test_format_validation_report_mixed_errors(self):
        errors = [
            "Row 2: 'printNumber' is empty.",
            "Row 12: Quantity mismatch for 'Peace Offering' (B101). Sum of parsed lots (2) does not equal totalQtyOwned (5) (notes: '2026-09-19: 2').",
        ]
        report = format_validation_report(errors, error_csv_path=None)
        self.assertIn("  - Row 2: 'printNumber' is empty.", report)
        self.assertIn("| Row | Print Num | Name", report)
        self.assertIn("| 12  | B101      | Peace Offering", report)
        self.assertIn("Validation found 1 total lot mismatch across the CSV.", report)
        self.assertNotIn("Full error report written to:", report)

    def test_format_validation_report_preserves_truncation_notice(self):
        errors = [
            "Row 12: Quantity mismatch for 'Peace Offering' (B101). Sum of parsed lots (2) does not equal totalQtyOwned (5) (notes: '2026-09-19: 2').",
            "... (truncated 15 additional row errors; 16 total errors encountered across CSV)",
            "Full error report written to: data/validation_errors.csv",
        ]
        report = format_validation_report(errors)
        self.assertIn("| Row | Print Num | Name", report)
        self.assertIn("... (truncated 15 additional row errors; 16 total errors encountered across CSV)", report)
        self.assertIn("Full error report written to: data/validation_errors.csv", report)

    def test_future_acquisition_date_in_column_rejected(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price,acquisitionDate
Towerfall,Welcome to Night City - Beta,B034,Standard,1,1.50,2099-01-01
"""
        path = self._create_csv("future_date_col.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertFalse(is_valid)
        self.assertEqual(len(rows), 0)
        self.assertEqual(len(errors), 1)
        self.assertTrue(any("is in the future" in err for err in errors))
        self.assertTrue(any("2099-01-01" in err for err in errors))
        self.assertTrue(any("Towerfall" in err and "B034" in err for err in errors))

    def test_future_acquisition_date_in_notes_rejected(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price,notes
Towerfall,Welcome to Night City - Beta,B034,Standard,1,1.50,2099-01-01
"""
        path = self._create_csv("future_date_notes.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertFalse(is_valid)
        self.assertEqual(len(rows), 0)
        self.assertEqual(len(errors), 1)
        self.assertTrue(any("is in the future" in err for err in errors))
        self.assertTrue(any("2099-01-01" in err for err in errors))
        self.assertTrue(any("Towerfall" in err and "B034" in err and "(notes: '2099-01-01')" in err for err in errors))

    def test_future_acquisition_date_sealed_rejected(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price,notes
Night City Booster Box,Welcome to Night City - Beta,,Standard,1,100.00,2099-01-01
"""
        path = self._create_csv("future_date_sealed.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertFalse(is_valid)
        self.assertEqual(len(rows), 0)
        self.assertEqual(len(errors), 1)
        self.assertTrue(any("is in the future" in err for err in errors))
        self.assertTrue(any("Night City Booster Box" in err and "SEALED" in err and "(notes: '2099-01-01')" in err for err in errors))

    def test_duplicate_dateless_card_rows_rejected(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price
Towerfall,Welcome to Night City - Beta,B034,Standard,1,1.50
Towerfall,Welcome to Night City - Beta,B034,Standard,2,1.50
"""
        path = self._create_csv("dupe_dateless.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertFalse(is_valid)
        self.assertTrue(any("Duplicate card" in err and "no acquisition date" in err for err in errors))

    def test_duplicate_dateless_sealed_rows_rejected(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price
Night City Booster Box,Welcome to Night City - Beta,,Standard,1,100.00
Night City Booster Box,Welcome to Night City - Beta,,Standard,2,100.00
"""
        path = self._create_csv("dupe_dateless_sealed.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertFalse(is_valid)
        self.assertTrue(any("Duplicate sealed item" in err and "no acquisition date" in err for err in errors))

    def test_different_finishes_dateless_not_flagged_as_duplicate(self):
        csv_content = """name,expansion,printNumber,finish,totalQtyOwned,price
Towerfall,Welcome to Night City - Beta,B034,Standard,1,1.50
Towerfall,Welcome to Night City - Beta,B034,Foil,1,5.00
"""
        path = self._create_csv("different_finish_dateless.csv", csv_content)
        is_valid, errors, rows = validate_collection_file(path)

        self.assertTrue(is_valid)
        self.assertEqual(len(rows), 2)


if __name__ == "__main__":
    unittest.main()
