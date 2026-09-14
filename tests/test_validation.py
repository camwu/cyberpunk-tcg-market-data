"""
Automated unit tests for collection CSV schema and data integrity validation.
"""

import tempfile
import unittest
from pathlib import Path

from tracker.validation import validate_collection_file, validate_sealed_file


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
        csv_content = """productId,name,expansion,totalQtyOwned,price
714346,Welcome to Night City - Beta Booster Box,Welcome to Night City - Beta,1,216.08
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
        self.assertEqual(rows[0]["item_type"], "Sealed")

    def test_missing_sealed_columns(self):
        csv_content = """productId,name
714346,Welcome to Night City - Beta Booster Box
"""
        path = self._create_csv("missing_cols_sealed.csv", csv_content)
        is_valid, errors, rows = validate_sealed_file(path)

        self.assertFalse(is_valid)
        self.assertEqual(len(rows), 0)
        self.assertTrue(any("Missing required columns" in err for err in errors))

    def test_invalid_sealed_fields(self):
        csv_content = """productId,name,expansion,totalQtyOwned,price
invalid_id,,Welcome to Night City,0,-5.00
"""
        path = self._create_csv("invalid_sealed.csv", csv_content)
        is_valid, errors, rows = validate_sealed_file(path)

        self.assertFalse(is_valid)
        self.assertTrue(any("must be an integer" in err for err in errors))
        self.assertTrue(any("'name' is empty" in err for err in errors))
        self.assertTrue(any("must be at least 1" in err for err in errors))
        self.assertTrue(any("must be non-negative" in err for err in errors))


if __name__ == "__main__":
    unittest.main()
