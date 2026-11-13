"""
Shared Iconic comparison utilities for market price extraction, quantile math,
and fail-closed CardNexus collection CSV row validation.
"""

import csv
from dataclasses import dataclass
import statistics
from typing import Any, List, Optional, Tuple

from tracker.validation import CollectionValidationError, REQUIRED_COLUMNS, parse_multi_lot_notes


def normalize_finish(raw_finish: str, default: str = "") -> str:
    """Normalizes CardNexus finish aliases while mapping empty input to default."""
    cleaned = (raw_finish or "").strip()
    upper = cleaned.upper()
    if upper in ("NORMAL", "STANDARD"):
        return "Standard"
    if upper == "FOIL":
        return "Foil"
    if not cleaned:
        return default
    return cleaned


def compute_quantiles(
    values: List[float],
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    """
    Computes (min, p25, median, p75, max) for a numeric sequence.
    Returns (None, None, None, None, None) when empty, and (min, None, median, None, max) when len < 4.
    """
    if not values:
        return None, None, None, None, None
    sorted_vals = sorted(values)
    v_min = sorted_vals[0]
    v_max = sorted_vals[-1]
    v_med = statistics.median(sorted_vals)
    if len(sorted_vals) >= 4:
        q1, _, q3 = statistics.quantiles(sorted_vals, n=4, method="inclusive")
        return v_min, q1, v_med, q3, v_max
    return v_min, None, v_med, None, v_max


def extract_finish_price(p_data: Any, preferred_finish: str) -> Optional[float]:
    """Extracts non-zero market price strictly for preferred_finish, accepting Normal/Standard aliases."""
    if not isinstance(p_data, dict):
        return None
    finish_variants = [preferred_finish]
    if preferred_finish == "Standard":
        finish_variants.append("Normal")
    elif preferred_finish == "Normal":
        finish_variants.append("Standard")

    for finish_key in finish_variants:
        sub = p_data.get(finish_key)
        if isinstance(sub, dict):
            mp = sub.get("marketPrice")
            if mp is not None and isinstance(mp, (int, float)) and float(mp) > 0.0:
                return float(mp)

    return None


@dataclass
class ValidatedCollectionRow:
    expansion: str
    print_number: str
    raw_finish: str
    norm_finish: str
    qty: int
    lots: List[Tuple[Optional[str], int]]


def load_validated_collection_rows(collection_path: str) -> List[ValidatedCollectionRow]:
    """Reads a collection CSV and validates positive-quantity acquisition lot notes once."""
    validated_rows: List[ValidatedCollectionRow] = []
    with open(collection_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing_columns = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing_columns:
            raise CollectionValidationError(f"Missing required columns: {sorted(missing_columns)}")
        for row in reader:
            exp = (row.get("expansion") or "").strip()
            pnum = (row.get("printNumber") or "").strip()
            raw_finish = (row.get("finish") or "").strip()
            norm_finish = normalize_finish(raw_finish, default="")
            qty_str = (row.get("totalQtyOwned") or "0").strip()

            try:
                qty = int(qty_str)
            except ValueError:
                raise CollectionValidationError(
                    f"Row {exp} #{pnum}: 'totalQtyOwned' must be an integer (got '{qty_str}')."
                )
            if qty < 0:
                raise CollectionValidationError(
                    f"Row {exp} #{pnum}: 'totalQtyOwned' must not be negative (got '{qty_str}')."
                )

            lots: List[Tuple[Optional[str], int]] = []
            if qty > 0:
                notes = (row.get("notes") or "").strip()
                lots, lot_err = parse_multi_lot_notes(notes, qty)
                if lot_err:
                    raise CollectionValidationError(f"Row {exp} #{pnum}: {lot_err}")

            validated_rows.append(
                ValidatedCollectionRow(
                    expansion=exp,
                    print_number=pnum,
                    raw_finish=raw_finish,
                    norm_finish=norm_finish,
                    qty=qty,
                    lots=lots,
                )
            )
    return validated_rows
