"""
CSV schema and data validation for Cyberpunk TCG collection imports.
Ensures structural correctness before database ingestion.
"""

import csv
import datetime
import os
from typing import List, Tuple

REQUIRED_COLUMNS = {"name", "expansion", "printNumber", "finish", "totalQtyOwned"}
REQUIRED_SEALED_COLUMNS = {"productId", "name", "expansion", "totalQtyOwned", "acquisitionDate"}
VALID_FINISHES = {"standard", "foil"}
FINISH_ALIASES = {"normal": "Standard"}


class CollectionValidationError(Exception):
    """Raised when a collection CSV fails schema or data integrity validation."""

    def __init__(self, message: str, errors: List[str] = None):
        super().__init__(message)
        self.errors = errors or []


def validate_collection_file(csv_path: str, max_row_errors: int = 5) -> Tuple[bool, List[str], List[dict]]:
    """
    Validates a collection CSV file against mandatory schema and data integrity constraints.
    Returns (is_valid, list_of_error_strings, list_of_validated_rows).
    """
    errors: List[str] = []
    validated_rows: List[dict] = []

    if not os.path.exists(csv_path):
        return False, [f"Collection file not found: {csv_path}"], []

    if os.path.isdir(csv_path):
        return False, [f"Expected a CSV file, but found a directory: {csv_path}"], []

    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return False, ["CSV file is empty or missing header row."], []

            reader.fieldnames = [h.strip() for h in reader.fieldnames if h is not None]
            if not reader.fieldnames or not any(reader.fieldnames):
                return False, ["CSV file is empty or missing header row."], []

            headers = set(reader.fieldnames)
            missing = REQUIRED_COLUMNS - headers
            if missing:
                errors.append(f"Missing required columns: {sorted(missing)} (required: {sorted(REQUIRED_COLUMNS)})")

            row_count = 0
            row_errors = 0
            for row_idx, row in enumerate(reader, start=2):
                row_count += 1
                if missing:
                    continue

                name = (row.get("name") or "").strip()
                expansion = (row.get("expansion") or "").strip()
                print_number = (row.get("printNumber") or "").strip()
                finish = (row.get("finish") or "").strip()
                qty_raw = (row.get("totalQtyOwned") or "").strip()
                price_raw = (row.get("price") or "").strip()

                if not name:
                    errors.append(f"Row {row_idx}: 'name' is empty.")
                    row_errors += 1
                if not expansion:
                    errors.append(f"Row {row_idx}: 'expansion' is empty.")
                    row_errors += 1
                if not print_number:
                    errors.append(f"Row {row_idx}: 'printNumber' is empty.")
                    row_errors += 1

                normalized_finish = finish
                if not finish:
                    errors.append(f"Row {row_idx}: 'finish' is empty.")
                    row_errors += 1
                else:
                    finish_key = finish.lower()
                    if finish_key in FINISH_ALIASES:
                        normalized_finish = FINISH_ALIASES[finish_key]
                    elif finish_key in VALID_FINISHES:
                        normalized_finish = "Foil" if finish_key == "foil" else "Standard"
                    else:
                        errors.append(f"Row {row_idx}: 'finish' must be 'Standard' or 'Foil' (got '{finish}').")
                        row_errors += 1

                qty = 0
                try:
                    qty = int(qty_raw)
                    if qty < 1:
                        errors.append(f"Row {row_idx}: 'totalQtyOwned' must be at least 1 (got '{qty_raw}').")
                        row_errors += 1
                except (ValueError, TypeError):
                    errors.append(f"Row {row_idx}: 'totalQtyOwned' must be an integer (got '{qty_raw}').")
                    row_errors += 1

                price = 0.0
                if price_raw:
                    try:
                        price = float(price_raw)
                        if price < 0.0:
                            errors.append(f"Row {row_idx}: 'price' must be non-negative (got '{price_raw}').")
                            row_errors += 1
                    except (ValueError, TypeError):
                        errors.append(f"Row {row_idx}: 'price' must be a valid number (got '{price_raw}').")
                        row_errors += 1

                if row_errors >= max_row_errors:
                    errors.append(f"... (truncated additional row errors after {max_row_errors} issues)")
                    break

                # Prepare parsed row dictionary
                row_copy = dict(row)
                row_copy["name"] = name
                row_copy["expansion"] = expansion
                row_copy["printNumber"] = print_number
                row_copy["finish"] = normalized_finish
                row_copy["totalQtyOwned"] = qty
                row_copy["price"] = price
                row_copy["item_type"] = "Card"
                validated_rows.append(row_copy)

            if row_count == 0 and not errors:
                errors.append("Collection CSV contains 0 card rows.")

    except UnicodeDecodeError as e:
        return False, [f"Unable to decode CSV as UTF-8: {e}"], []
    except Exception as e:
        return False, [f"Failed to read CSV file: {e}"], []

    is_valid = len(errors) == 0
    return is_valid, errors, validated_rows if is_valid else []


def validate_sealed_file(csv_path: str, max_row_errors: int = 5) -> Tuple[bool, List[str], List[dict]]:
    """
    Validates a sealed inventory CSV file against schema and data constraints.
    Returns (is_valid, list_of_error_strings, list_of_validated_rows).
    """
    errors: List[str] = []
    validated_rows: List[dict] = []

    if not os.path.exists(csv_path):
        return False, [f"Sealed inventory file not found: {csv_path}"], []

    if os.path.isdir(csv_path):
        return False, [f"Expected a CSV file, but found a directory: {csv_path}"], []

    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return False, ["Sealed CSV file is empty or missing header row."], []

            reader.fieldnames = [h.strip() for h in reader.fieldnames if h is not None]
            if not reader.fieldnames or not any(reader.fieldnames):
                return False, ["Sealed CSV file is empty or missing header row."], []

            headers = set(reader.fieldnames)
            missing = REQUIRED_SEALED_COLUMNS - headers
            if missing:
                errors.append(f"Missing required columns in sealed CSV: {sorted(missing)} (required: {sorted(REQUIRED_SEALED_COLUMNS)})")

            row_count = 0
            row_errors = 0
            seen_lots = set()
            for row_idx, row in enumerate(reader, start=2):
                row_count += 1
                if missing:
                    continue

                prod_id_raw = (row.get("productId") or "").strip()
                name = (row.get("name") or "").strip()
                expansion = (row.get("expansion") or "").strip()
                qty_raw = (row.get("totalQtyOwned") or "").strip()
                price_raw = (row.get("price") or "").strip()
                finish = (row.get("finish") or "Standard").strip()
                acq_date_raw = (row.get("acquisitionDate") or "").strip()

                prod_id = None
                try:
                    prod_id = int(prod_id_raw)
                    if prod_id <= 0:
                        errors.append(f"Row {row_idx}: 'productId' must be a positive integer (got '{prod_id_raw}').")
                        row_errors += 1
                except (ValueError, TypeError):
                    errors.append(f"Row {row_idx}: 'productId' must be an integer (got '{prod_id_raw}').")
                    row_errors += 1

                if not name:
                    errors.append(f"Row {row_idx}: 'name' is empty.")
                    row_errors += 1
                if not expansion:
                    errors.append(f"Row {row_idx}: 'expansion' is empty.")
                    row_errors += 1

                if not acq_date_raw:
                    errors.append(f"Row {row_idx}: 'acquisitionDate' is required.")
                    row_errors += 1
                else:
                    try:
                        datetime.date.fromisoformat(acq_date_raw)
                    except ValueError:
                        errors.append(f"Row {row_idx}: 'acquisitionDate' must be in YYYY-MM-DD format (got '{acq_date_raw}').")
                        row_errors += 1

                if prod_id and acq_date_raw:
                    lot_key = (prod_id, acq_date_raw)
                    if lot_key in seen_lots:
                        errors.append(f"Row {row_idx}: Duplicate sealed lot for productId {prod_id} and acquisitionDate '{acq_date_raw}'. Combine quantities using 'totalQtyOwned'.")
                        row_errors += 1
                    else:
                        seen_lots.add(lot_key)

                qty = 0
                try:
                    qty = int(qty_raw)
                    if qty < 1:
                        errors.append(f"Row {row_idx}: 'totalQtyOwned' must be at least 1 (got '{qty_raw}').")
                        row_errors += 1
                except (ValueError, TypeError):
                    errors.append(f"Row {row_idx}: 'totalQtyOwned' must be an integer (got '{qty_raw}').")
                    row_errors += 1

                price = 0.0
                if price_raw:
                    try:
                        price = float(price_raw)
                        if price < 0.0:
                            errors.append(f"Row {row_idx}: 'price' must be non-negative (got '{price_raw}').")
                            row_errors += 1
                    except (ValueError, TypeError):
                        errors.append(f"Row {row_idx}: 'price' must be a valid number (got '{price_raw}').")
                        row_errors += 1

                if row_errors >= max_row_errors:
                    errors.append(f"... (truncated additional sealed row errors after {max_row_errors} issues)")
                    break

                row_copy = dict(row)
                row_copy["productId"] = prod_id
                row_copy["name"] = name
                row_copy["expansion"] = expansion
                row_copy["finish"] = finish or "Standard"
                row_copy["totalQtyOwned"] = qty
                row_copy["price"] = price
                row_copy["acquisitionDate"] = acq_date_raw
                row_copy["item_type"] = "Sealed"
                validated_rows.append(row_copy)

    except UnicodeDecodeError as e:
        return False, [f"Unable to decode sealed CSV as UTF-8: {e}"], []
    except Exception as e:
        return False, [f"Failed to read sealed CSV file: {e}"], []

    is_valid = len(errors) == 0
    return is_valid, errors, validated_rows if is_valid else []
