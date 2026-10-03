"""
CSV schema and data validation for Cyberpunk TCG collection imports.
Ensures structural correctness before database ingestion.
"""

import csv
import datetime
import os
import re
from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple

REQUIRED_COLUMNS = {"name", "expansion", "printNumber", "finish", "totalQtyOwned"}
REQUIRED_SEALED_COLUMNS = {"productId", "name", "expansion", "totalQtyOwned", "acquisitionDate"}
VALID_FINISHES = {"standard", "foil"}
FINISH_ALIASES = {"normal": "Standard"}
SEALED_KEYWORDS = (
    "booster box",
    "booster pack",
    "starter deck",
    "display",
    "box case",
    "booster case",
    "sealed",
    "alpha kit",
    "bundle",
    "blister",
)


def extract_date_from_text(text: Optional[str]) -> Optional[str]:
    """Extracts the first valid YYYY-MM-DD date pattern from text, or returns None."""
    if not text:
        return None
    match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", str(text).strip())
    if match:
        date_candidate = match.group(0)
        try:
            datetime.date.fromisoformat(date_candidate)
            return date_candidate
        except ValueError:
            return None
    return None


def parse_multi_lot_notes(notes: Optional[str], total_qty: int) -> Tuple[List[Tuple[Optional[str], int]], Optional[str]]:
    """
    Parses acquisition date(s) and lot quantities from the notes field.
    Supports:
    - Standalone single date: '2026-09-02' -> [(2026-09-02, total_qty)]
    - Semicolon or comma delimited lots: '2026-09-02: 2; 2026-09-18: 3'
    - Implicit quantities in delimited lists: '2026-09-02; 2026-09-18' (1 each)
    - Mixed implicit/explicit: '2026-09-02; 2026-09-18: 2'
    Returns:
    (list_of_lots, error_message_or_None) where each lot is (date_str_or_None, qty).
    """
    if not notes or not str(notes).strip():
        return [(None, total_qty)], None

    text = str(notes).strip()
    chunks = [c.strip() for c in re.split(r"[;,]", text) if c.strip()]
    if not chunks:
        return [(None, total_qty)], None

    if len(chunks) == 1:
        m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b(?:\s*:\s*(-?\d+))?", chunks[0])
        if m:
            date_candidate = m.group(1)
            try:
                datetime.date.fromisoformat(date_candidate)
            except ValueError:
                return [(None, total_qty)], None

            explicit_qty_str = m.group(2)
            if explicit_qty_str is not None:
                qty = int(explicit_qty_str)
                if qty < 1:
                    return [], f"Parsed lot quantity for '{date_candidate}' must be at least 1 (got {qty})."
                if qty != total_qty:
                    return [(date_candidate, qty)], f"Sum of parsed lots ({qty}) does not equal totalQtyOwned ({total_qty})."
                return [(date_candidate, qty)], None
            return [(date_candidate, total_qty)], None
        return [(None, total_qty)], None

    lots: List[Tuple[Optional[str], int]] = []
    for c in chunks:
        m = re.search(r"\b(\d{4}-\d{2}-\d{2})\b(?:\s*:\s*(-?\d+))?", c)
        if not m:
            continue
        date_candidate = m.group(1)
        try:
            datetime.date.fromisoformat(date_candidate)
        except ValueError:
            continue

        explicit_qty_str = m.group(2)
        if explicit_qty_str is not None:
            qty = int(explicit_qty_str)
            if qty < 1:
                return [], f"Parsed lot quantity for '{date_candidate}' must be at least 1 (got {qty})."
        else:
            qty = 1
        lots.append((date_candidate, qty))

    if not lots:
        return [(None, total_qty)], None

    lot_sum = sum(q for _, q in lots)
    if lot_sum != total_qty:
        return lots, f"Sum of parsed lots ({lot_sum}) does not equal totalQtyOwned ({total_qty})."

    return lots, None


def is_sealed_product(name: str, expansion: str = "") -> bool:
    """Detects whether a product is sealed based on naming conventions."""
    clean_name = (name or "").strip().lower()
    return any(keyword in clean_name for keyword in SEALED_KEYWORDS)


class CollectionValidationError(Exception):
    """Raised when a collection CSV fails schema or data integrity validation."""

    def __init__(self, message: str, errors: List[str] = None):
        super().__init__(message)
        self.errors = errors or []


def format_validation_report(
    errors: List[str],
    max_table_rows: int = 25,
    error_csv_path: Optional[str] = None,
) -> str:
    """
    Renders validation error diagnostics.
    Formats lot quantity mismatches as an aligned 6-column CLI table with summary and export notice,
    while rendering non-mismatch issues as bulleted points.
    """
    if not errors:
        return ""

    mismatches = []
    other_errors = []
    truncation_notices = []
    discovered_csv_path = error_csv_path

    mismatch_pattern = re.compile(
        r"^Row\s+(\d+):\s+Quantity mismatch for '([^']+)'\s+\(([^)]+)\)\.\s+Sum of parsed lots \((\d+)\) does not equal totalQtyOwned \((\d+)\)\.?\s+\(notes:\s*'([^']*)'\)\.?$"
    )

    for err in errors:
        if err.startswith("... (truncated"):
            truncation_notices.append(err)
            continue
        if err.startswith("Full error report written to:"):
            if not discovered_csv_path:
                discovered_csv_path = err.split("Full error report written to:", 1)[1].strip()
            continue
        m = mismatch_pattern.match(err)
        if m:
            row_idx, name, print_num, parsed, total, notes = m.groups()
            mismatches.append({
                "row": row_idx,
                "print_number": print_num,
                "name": name,
                "parsed": parsed,
                "qty": total,
                "notes": notes,
            })
        else:
            other_errors.append(err)

    output = []
    if other_errors:
        for err in other_errors:
            output.append(f"  - {err}")
        if mismatches:
            output.append("")

    if mismatches:
        rows_to_show = mismatches[:max_table_rows]
        col_row = max(len("Row"), max(len(r["row"]) for r in rows_to_show))
        col_print_num = max(len("Print Num"), max(len(r["print_number"]) for r in rows_to_show))
        col_name = max(len("Name"), max(len(r["name"]) for r in rows_to_show))
        col_parsed = len("Parsed")
        col_qty = len("Qty")
        col_notes = max(len("Notes"), max(len(r["notes"]) for r in rows_to_show))

        header = f"| {'Row':<{col_row}} | {'Print Num':<{col_print_num}} | {'Name':<{col_name}} | {'Parsed':<{col_parsed}} | {'Qty':<{col_qty}} | {'Notes':<{col_notes}} |"
        sep = f"|{'-' * (col_row + 2)}|{'-' * (col_print_num + 2)}|{'-' * (col_name + 2)}|{'-' * (col_parsed + 2)}|{'-' * (col_qty + 2)}|{'-' * (col_notes + 2)}|"

        output.append(header)
        output.append(sep)
        for r in rows_to_show:
            row_str = f"| {r['row']:<{col_row}} | {r['print_number']:<{col_print_num}} | {r['name']:<{col_name}} | {r['parsed']:<{col_parsed}} | {r['qty']:<{col_qty}} | {r['notes']:<{col_notes}} |"
            output.append(row_str)

        output.append("")
        total_mismatches = len(mismatches)
        if total_mismatches > max_table_rows:
            output.append(f"Validation found {total_mismatches} total lot mismatches ({max_table_rows} shown above).")
        else:
            output.append(f"Validation found {total_mismatches} total lot mismatch{'es' if total_mismatches != 1 else ''} across the CSV.")

    if truncation_notices:
        for t in truncation_notices:
            output.append(t)

    if discovered_csv_path and mismatches:
        output.append(f"Full error report written to: {discovered_csv_path}")

    return "\n".join(output)


DEFAULT_ERROR_EXPORT = "__DEFAULT__"


class LotResolutionResult(NamedTuple):
    lots: List[Tuple[Optional[str], int]]
    errors: List[str]
    mismatches: List[Dict[str, str]]


class DuplicateTracker:
    """Tracks seen lots across collection and sealed files to prevent duplicate entries."""

    def __init__(self) -> None:
        self.seen_sealed_lots: Set[Tuple[str, str, str]] = set()
        self.seen_card_lots: Set[Tuple[str, str, str, str]] = set()
        self.seen_dateless_cards: Set[Tuple[str, str, str]] = set()
        self.seen_dateless_sealed: Set[Tuple[str, str]] = set()
        self.seen_sealed_file_lots: Set[Tuple[int, str]] = set()

    def check_and_record(
        self,
        row_data: Dict[str, Any],
        lots: List[Tuple[Optional[str], int]],
        row_idx: int,
        errors: List[str],
    ) -> bool:
        """Validates lot uniqueness for collection entries, mutating seen sets in-place."""
        is_sealed = row_data["is_sealed"]
        name = row_data["name"]
        expansion = row_data["expansion"]
        print_number = row_data.get("printNumber")
        normalized_finish = row_data["finish"]

        for lot_date, _ in lots:
            if is_sealed and lot_date:
                lot_key = (name.lower(), expansion.lower(), lot_date)
                if lot_key in self.seen_sealed_lots:
                    errors.append(f"Row {row_idx}: Duplicate sealed lot for '{name}' and acquisitionDate '{lot_date}'. Combine quantities using 'totalQtyOwned'.")
                    return False
                self.seen_sealed_lots.add(lot_key)
            elif not is_sealed and lot_date:
                card_lot_key = (expansion.lower(), (print_number or "").lower(), normalized_finish.lower(), lot_date)
                if card_lot_key in self.seen_card_lots:
                    errors.append(f"Row {row_idx}: Duplicate card lot for '{name}' ({print_number}, {normalized_finish}) and acquisitionDate '{lot_date}'. Combine quantities using 'totalQtyOwned' or notes.")
                    return False
                self.seen_card_lots.add(card_lot_key)
            elif not is_sealed and not lot_date:
                dateless_key = (expansion.lower(), (print_number or "").lower(), normalized_finish.lower())
                if dateless_key in self.seen_dateless_cards:
                    errors.append(
                        f"Row {row_idx}: Duplicate card '{name}' ({print_number}, {normalized_finish}) with no acquisition date. "
                        f"Add an acquisition date to the notes field to distinguish purchase lots, or combine quantities using 'totalQtyOwned'."
                    )
                    return False
                self.seen_dateless_cards.add(dateless_key)
            elif is_sealed and not lot_date:
                dateless_sealed_key = (name.lower(), expansion.lower())
                if dateless_sealed_key in self.seen_dateless_sealed:
                    errors.append(
                        f"Row {row_idx}: Duplicate sealed item '{name}' ({expansion}) with no acquisition date. "
                        f"Add an acquisition date to the notes field to distinguish purchase lots, or combine quantities using 'totalQtyOwned'."
                    )
                    return False
                self.seen_dateless_sealed.add(dateless_sealed_key)
        return True

    def check_and_record_sealed_file(
        self,
        prod_id: int,
        acq_date: str,
        row_idx: int,
        errors: List[str],
    ) -> bool:
        """Validates lot uniqueness for standalone sealed files, mutating seen sets in-place."""
        if prod_id and acq_date:
            lot_key = (prod_id, acq_date)
            if lot_key in self.seen_sealed_file_lots:
                errors.append(f"Row {row_idx}: Duplicate sealed lot for productId {prod_id} and acquisitionDate '{acq_date}'. Combine quantities using 'totalQtyOwned'.")
                return False
            self.seen_sealed_file_lots.add(lot_key)
        return True


def _parse_and_validate_row(
    row: Dict[str, Any],
    row_idx: int,
    is_sealed: bool = False,
    include_desc: bool = True,
    require_product_id: bool = False,
) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """Validates common row fields, normalizes types and finishes, and collects formatting errors."""
    errors: List[str] = []
    name = (row.get("name") or "").strip()
    expansion = (row.get("expansion") or "").strip()
    print_number = (row.get("printNumber") or "").strip()
    prod_id_raw = (row.get("productId") or "").strip()

    # Item-type precedence:
    # 1. Caller-supplied is_sealed parameter (e.g. from validate_sealed_file)
    # 2. Explicit item_type column in unified collection exports
    # 3. Keyword heuristic via is_sealed_product(name, expansion)
    is_sealed = is_sealed or (row.get("item_type") == "Sealed") or is_sealed_product(name, expansion)
    item_type = "Sealed" if is_sealed else "Card"
    print_num_val = print_number or prod_id_raw or ("SEALED" if is_sealed else "N/A")

    item_desc = f" for '{name}' ({print_num_val})" if (include_desc and name) else ((f" ({print_num_val})" if print_num_val != "N/A" else "") if include_desc else "")

    if not name:
        if include_desc:
            name_ctx = f" (printNumber: '{print_number}')" if print_number else (f" (productId: '{prod_id_raw}')" if prod_id_raw else "")
            errors.append(f"Row {row_idx}: 'name' is empty{name_ctx}.")
        else:
            errors.append(f"Row {row_idx}: 'name' is empty.")
    if not expansion:
        errors.append(f"Row {row_idx}: 'expansion' is empty{item_desc}.")

    if not is_sealed and not print_number:
        pn_desc = f" for '{name}'" if name else ""
        errors.append(f"Row {row_idx}: 'printNumber' is empty{pn_desc}.")

    normalized_finish = "Standard"
    finish = (row.get("finish") or "").strip()
    if finish:
        finish_key = finish.lower()
        if finish_key in FINISH_ALIASES:
            normalized_finish = FINISH_ALIASES[finish_key]
        elif finish_key in VALID_FINISHES:
            normalized_finish = "Foil" if finish_key == "foil" else "Standard"
        else:
            errors.append(f"Row {row_idx}: 'finish' must be 'Standard' or 'Foil'{item_desc} (got '{finish}').")
    elif not is_sealed:
        errors.append(f"Row {row_idx}: 'finish' is empty{item_desc}.")

    qty_raw = (row.get("totalQtyOwned") or "").strip()
    qty = 0
    try:
        qty = int(qty_raw)
        if qty < 1:
            errors.append(f"Row {row_idx}: 'totalQtyOwned' must be at least 1{item_desc} (got '{qty_raw}').")
    except (ValueError, TypeError):
        errors.append(f"Row {row_idx}: 'totalQtyOwned' must be an integer{item_desc} (got '{qty_raw}').")

    price_raw = (row.get("price") or "").strip()
    price = 0.0
    if price_raw:
        try:
            price = float(price_raw)
            if price < 0.0:
                errors.append(f"Row {row_idx}: 'price' must be non-negative{item_desc} (got '{price_raw}').")
        except (ValueError, TypeError):
            errors.append(f"Row {row_idx}: 'price' must be a valid number{item_desc} (got '{price_raw}').")

    prod_id = None
    if require_product_id:
        try:
            prod_id = int(prod_id_raw)
            if prod_id <= 0:
                errors.append(f"Row {row_idx}: 'productId' must be a positive integer (got '{prod_id_raw}').")
        except (ValueError, TypeError):
            errors.append(f"Row {row_idx}: 'productId' must be an integer (got '{prod_id_raw}').")
    elif prod_id_raw:
        try:
            prod_id = int(prod_id_raw)
        except (ValueError, TypeError):
            prod_id = None

    if errors:
        return None, errors

    parsed_data = {
        "row": row,
        "name": name,
        "expansion": expansion,
        "printNumber": print_number or None,
        "finish": normalized_finish,
        "totalQtyOwned": qty,
        "price": price,
        "productId": prod_id,
        "is_sealed": is_sealed,
        "item_type": item_type,
        "print_num_val": print_num_val,
        "item_desc": item_desc,
        "notes": (row.get("notes") or "").strip(),
        "acquisitionDate": (row.get("acquisitionDate") or "").strip(),
    }
    return parsed_data, []


def _resolve_row_lots(
    raw_notes: Optional[str],
    acq_date_col: Optional[str],
    qty: int,
    today_str: str,
    item_info: Dict[str, Any],
) -> LotResolutionResult:
    """Parses lot purchase tranches, validates date formats, and enforces future-date bounds."""
    errors: List[str] = []
    mismatches: List[Dict[str, str]] = []
    lots: List[Tuple[Optional[str], int]] = []

    name = item_info.get("name", "")
    print_num_val = item_info.get("print_num_val", "")
    row_idx = item_info.get("row_idx", 0)
    item_desc = item_info.get("item_desc", "")

    acq_date_future_flagged = False
    if acq_date_col and not extract_date_from_text(acq_date_col):
        errors.append(f"Row {row_idx}: 'acquisitionDate' must be in YYYY-MM-DD format{item_desc} (got '{acq_date_col}').")
    elif acq_date_col and acq_date_col > today_str:
        errors.append(f"Row {row_idx}: Acquisition date '{acq_date_col}' for '{name}' ({print_num_val}) is in the future.")
        acq_date_future_flagged = True

    if qty >= 1:
        if raw_notes and extract_date_from_text(raw_notes):
            raw_to_parse = raw_notes
        elif acq_date_col:
            raw_to_parse = acq_date_col
        else:
            raw_to_parse = raw_notes or ""

        parsed_lots, lot_err = parse_multi_lot_notes(raw_to_parse, qty)
        if lot_err:
            errors.append(f"Row {row_idx}: Quantity mismatch for '{name}' ({print_num_val}). {lot_err} (notes: '{raw_to_parse}')")
            parsed_qty = sum(q for _, q in parsed_lots)
            mismatches.append({
                "row": str(row_idx),
                "print_number": str(print_num_val),
                "name": name,
                "parsed": str(parsed_qty),
                "qty": str(qty),
                "notes": raw_to_parse,
            })
        else:
            future_lot_dates = [
                d for d, _ in parsed_lots
                if d and d > today_str and not (acq_date_future_flagged and d == acq_date_col)
            ]
            if future_lot_dates:
                for fdate in future_lot_dates:
                    errors.append(
                        f"Row {row_idx}: Acquisition date '{fdate}' for '{name}' ({print_num_val}) is in the future (notes: '{raw_to_parse}')."
                    )
            else:
                lots = parsed_lots

    return LotResolutionResult(lots=lots, errors=errors, mismatches=mismatches)


def _export_validation_errors(error_export_path: Optional[str], lot_mismatches: List[Dict[str, str]]) -> None:
    """Persists lot mismatch diagnostics to disk as a CSV report."""
    if not error_export_path or not lot_mismatches:
        return
    try:
        parent_dir = os.path.dirname(error_export_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(error_export_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["row", "print_number", "name", "parsed", "qty", "notes"])
            writer.writeheader()
            for m in lot_mismatches:
                writer.writerow(m)
    except OSError:
        pass


def _cleanup_validation_error_export(error_export_path: Optional[str]) -> None:
    """Removes stale error export files upon successful validation."""
    if error_export_path and os.path.exists(error_export_path):
        try:
            os.remove(error_export_path)
        except OSError:
            pass


def validate_collection_file(
    csv_path: str,
    max_row_errors: Optional[int] = 25,
    error_export_path: Optional[str] = DEFAULT_ERROR_EXPORT,
) -> Tuple[bool, List[str], List[dict]]:
    """Validates collection CSV against schema and data constraints; returns (is_valid, errors, rows)."""
    if error_export_path == DEFAULT_ERROR_EXPORT:
        error_export_path = os.path.join(os.path.dirname(os.path.abspath(csv_path)) if csv_path else ".", "validation_errors.csv")
    if not os.path.exists(csv_path):
        return False, [f"Collection file not found: {csv_path}"], []
    if os.path.isdir(csv_path):
        return False, [f"Expected a CSV file, but found a directory: {csv_path}"], []

    try:
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None or not any(reader.fieldnames):
                return False, ["CSV file is empty or missing header row."], []
            reader.fieldnames = [h.strip() for h in reader.fieldnames if h is not None]
            headers = set(reader.fieldnames)
            missing = REQUIRED_COLUMNS - headers
            if missing:
                return False, [f"Missing required columns: {sorted(missing)} (required: {sorted(REQUIRED_COLUMNS)})"], []

            errors, lot_mismatches, validated_rows = [], [], []
            tracker, today_str = DuplicateTracker(), datetime.date.today().isoformat()
            row_count = 0
            for row_idx, row in enumerate(reader, start=2):
                row_count += 1
                parsed, parse_errors = _parse_and_validate_row(row, row_idx, is_sealed=False, include_desc=True)
                if parse_errors:
                    errors.extend(parse_errors)
                    continue

                item_info = {"name": parsed["name"], "print_num_val": parsed["print_num_val"], "row_idx": row_idx, "item_desc": parsed["item_desc"]}
                lot_res = _resolve_row_lots(parsed["notes"], parsed["acquisitionDate"], parsed["totalQtyOwned"], today_str, item_info)
                if lot_res.errors:
                    errors.extend(lot_res.errors)
                    lot_mismatches.extend(lot_res.mismatches)
                    continue

                if not tracker.check_and_record(parsed, lot_res.lots, row_idx, errors):
                    continue

                for lot_date, lot_qty in lot_res.lots:
                    row_copy = {**dict(parsed["row"]), **{
                        "name": parsed["name"], "expansion": parsed["expansion"], "printNumber": parsed["printNumber"],
                        "finish": parsed["finish"], "totalQtyOwned": lot_qty, "price": parsed["price"],
                        "item_type": parsed["item_type"], "acquisitionDate": lot_date,
                    }}
                    if parsed["is_sealed"]:
                        row_copy["rarity"] = "Sealed"
                    validated_rows.append(row_copy)

            if row_count == 0 and not errors:
                errors.append("Collection CSV contains 0 inventory rows.")
    except UnicodeDecodeError as e:
        return False, [f"Unable to decode CSV as UTF-8: {e}"], []
    except (OSError, csv.Error) as e:
        return False, [f"Failed to read CSV file: {e}"], []

    if not errors:
        _cleanup_validation_error_export(error_export_path)
        return True, [], validated_rows

    _export_validation_errors(error_export_path, lot_mismatches)
    reported_errors = list(errors)
    if max_row_errors is not None and max_row_errors > 0 and len(errors) > max_row_errors:
        overflow = len(errors) - max_row_errors
        reported_errors = errors[:max_row_errors] + [f"... (truncated {overflow} additional row error{'s' if overflow != 1 else ''}; {len(errors)} total errors encountered across CSV)"]
    if error_export_path and lot_mismatches:
        reported_errors.append(f"Full error report written to: {error_export_path}")
    return False, reported_errors, []


def validate_sealed_file(
    csv_path: str,
    max_row_errors: Optional[int] = 25,
) -> Tuple[bool, List[str], List[dict]]:
    """Validates sealed inventory CSV against schema and data constraints; returns (is_valid, errors, rows)."""
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
                return False, [f"Missing required columns in sealed CSV: {sorted(missing)} (required: {sorted(REQUIRED_SEALED_COLUMNS)})"], []

            errors: List[str] = []
            validated_rows: List[dict] = []
            tracker = DuplicateTracker()

            for row_idx, row in enumerate(reader, start=2):
                parsed, parse_errors = _parse_and_validate_row(row, row_idx, is_sealed=True, include_desc=False, require_product_id=True)
                row_errors = list(parse_errors)
                acq_date_raw = (row.get("acquisitionDate") or "").strip()
                if not acq_date_raw:
                    row_errors.append(f"Row {row_idx}: 'acquisitionDate' is required.")
                else:
                    try:
                        datetime.date.fromisoformat(acq_date_raw)
                    except ValueError:
                        row_errors.append(f"Row {row_idx}: 'acquisitionDate' must be in YYYY-MM-DD format (got '{acq_date_raw}').")

                if parsed and parsed.get("productId") and acq_date_raw:
                    tracker.check_and_record_sealed_file(parsed["productId"], acq_date_raw, row_idx, row_errors)

                if row_errors:
                    errors.extend(row_errors)
                    continue

                row_copy = dict(row)
                row_copy.update({
                    "productId": parsed["productId"], "name": parsed["name"], "expansion": parsed["expansion"],
                    "finish": parsed["finish"], "totalQtyOwned": parsed["totalQtyOwned"], "price": parsed["price"],
                    "acquisitionDate": acq_date_raw, "item_type": "Sealed",
                })
                validated_rows.append(row_copy)
    except UnicodeDecodeError as e:
        return False, [f"Unable to decode sealed CSV as UTF-8: {e}"], []
    except (OSError, csv.Error) as e:
        return False, [f"Failed to read sealed CSV file: {e}"], []

    if not errors:
        return True, [], validated_rows

    reported_errors = list(errors)
    if max_row_errors is not None and max_row_errors > 0 and len(errors) > max_row_errors:
        overflow = len(errors) - max_row_errors
        reported_errors = errors[:max_row_errors] + [f"... (truncated {overflow} additional sealed row error{'s' if overflow != 1 else ''}; {len(errors)} total errors encountered across CSV)"]
    return False, reported_errors, []

