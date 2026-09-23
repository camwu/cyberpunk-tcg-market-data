"""
Purchase history and cost basis manager for Cyberpunk TCG collections.
Scans purchase documents, computes SHA-256 hashes to prevent redundant parsing,
maintains an on-disk cache and CSV ledger, and aggregates total invested capital.
"""

from dataclasses import dataclass, asdict
import csv
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

try:
    import pypdf
except ImportError:
    pypdf = None

SUPPORTED_EXTENSIONS = {".pdf", ".csv", ".txt", ".json"}


@dataclass
class PurchaseRecord:
    date: str
    merchant: str
    amount: float
    description: str
    filename: str
    sha256: str


def compute_file_sha256(filepath: str) -> str:
    """Computes deterministic SHA-256 hex digest for a file."""
    hasher = hashlib.sha256()
    with open(filepath, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def extract_text_from_document(filepath: str) -> str:
    """Extracts raw text content from PDF, CSV, TXT, or JSON file."""
    p = Path(filepath)
    ext = p.suffix.lower()

    if ext == ".pdf":
        if pypdf is None:
            return ""
        try:
            reader = pypdf.PdfReader(filepath)
            texts = []
            for page in reader.pages:
                t = page.extract_text() or ""
                texts.append(t)
            return "\n".join(texts)
        except Exception:
            return ""

    if ext in {".txt", ".csv", ".json"}:
        try:
            with open(filepath, "r", encoding="utf-8-sig", errors="ignore") as f:
                return f.read()
        except Exception:
            return ""

    return ""


def parse_date_candidate(text: str) -> Optional[str]:
    """Finds first ISO date (YYYY-MM-DD) or converts written dates (Month DD, YYYY)."""
    m_iso = re.search(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)", text)
    if m_iso:
        d_str = m_iso.group(1)
        try:
            datetime.date.fromisoformat(d_str)
            return d_str
        except ValueError:
            pass

    months = {
        "jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06",
        "jul": "07", "aug": "08", "sep": "09", "oct": "10", "nov": "11", "dec": "12",
    }
    m_written = re.search(r"(?<![a-zA-Z])(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+(\d{1,2}),?\s+(20\d{2})(?!\d)", text, re.IGNORECASE)
    if m_written:
        m_str = months.get(m_written.group(1)[:3].lower())
        d_str = f"{int(m_written.group(2)):02d}"
        y_str = m_written.group(3)
        if m_str:
            cand = f"{y_str}-{m_str}-{d_str}"
            try:
                datetime.date.fromisoformat(cand)
                return cand
            except ValueError:
                pass

    return None


def validate_amount_string(val_str: str) -> Optional[float]:
    """
    Validates that val_str represents a positive currency amount.
    Accepts positive integers (e.g. '10') or exactly two decimals (e.g. '10.00', '43.90').
    Rejects negatives, three+ decimals, spaces, or non-numeric characters.
    """
    if not isinstance(val_str, str):
        return None
    s = val_str.strip()
    if s.startswith("$"):
        s = s[1:].strip()
    if not re.match(r"^\d+(\.\d{2})?$", s):
        return None
    try:
        val = float(s)
        return round(val, 2) if val > 0 else None
    except ValueError:
        return None


def prompt_for_amount(
    filename: str,
    candidates: List[float],
    interactive: Optional[bool] = None,
) -> Optional[float]:
    """
    Prompts user interactively to resolve missing or ambiguous receipt totals.
    Falls back to bottom-most candidate or None in non-interactive environments.
    """
    if interactive is None:
        interactive = sys.stdin.isatty()

    if not interactive:
        if candidates:
            return candidates[-1]
        return None

    print(f"\n[Action Required] Total resolution needed for '{filename}':")
    if candidates:
        print(f"Detected {len(candidates)} candidate amounts in document text:")
        for idx, cand in enumerate(candidates, 1):
            suffix = " (recommended: bottom-most)" if idx == len(candidates) else ""
            print(f"  [{idx}] ${cand:,.2f}{suffix}")
        print("  [c] Enter custom amount")
        print("  [s] Skip this file")

        while True:
            choice = input("Select option or enter amount: ").strip()
            if choice.lower() in ("s", "skip"):
                return None
            if choice.isdigit() and 1 <= int(choice) <= len(candidates):
                return candidates[int(choice) - 1]
            if choice.lower() == "c":
                raw_amt = input("Enter total amount (e.g. 10 or 10.00): ").strip()
                val = validate_amount_string(raw_amt)
                if val is not None:
                    return val
                print("Invalid amount. Must be > 0 and formatted as an integer or two decimals (e.g. 10 or 10.00).")
                continue
            val = validate_amount_string(choice)
            if val is not None:
                return val
            print(f"Invalid input. Select a listed number [1-{len(candidates)}], enter an amount (e.g. 10.00), or 's' to skip.")
    else:
        print(f"No dollar amounts detected in document text for '{filename}'.")
        while True:
            choice = input("Enter total amount (e.g. 10 or 10.00), or 's' to skip: ").strip()
            if choice.lower() in ("s", "skip"):
                return None
            val = validate_amount_string(choice)
            if val is not None:
                return val
            print("Invalid amount. Must be > 0 and formatted as an integer or two decimals (e.g. 10 or 10.00).")


def parse_receipt_document(
    filepath: str,
    interactive: Optional[bool] = None,
) -> Optional[Tuple[str, str, float, str]]:
    """
    Parses date, merchant, amount, and description strictly from document text.
    Returns (date, merchant, amount, description) or None.
    """
    filename = Path(filepath).name
    text = extract_text_from_document(filepath)
    if not text.strip():
        print(f"Warning: Could not parse purchase details from '{filename}' (empty text). Skipping file.")
        return None

    # 1. Date extraction strictly from text
    date_cand = parse_date_candidate(text)
    if not date_cand:
        print(f"Warning: Could not extract purchase date from '{filename}'. Skipping file.")
        return None

    # 2. Merchant identification from text
    lower_text = text.lower()
    merchant = "Unknown"
    if "kickstarter" in lower_text:
        merchant = "Kickstarter"
    elif "backerkit" in lower_text:
        merchant = "BackerKit"
    elif "tcgplayer" in lower_text:
        merchant = "TCGplayer"
    elif "ebay" in lower_text:
        merchant = "eBay"
    elif "paper hero" in lower_text or "paperhero" in lower_text:
        merchant = "Paper Hero's Games"
    else:
        # Check for Statement name header in payment receipts
        m_stmt = re.search(r"Statement name\s*\n\s*([^\n\r]+)", text, re.IGNORECASE)
        if m_stmt:
            merchant = m_stmt.group(1).strip()

    # 3. Amount extraction
    amount: Optional[float] = None

    # Rule A: Explicit standard accounting labels
    patterns = [
        r"(?:Pledge total|Total charged to|Grand Total|Order Total)[\s:\$]*\$?([\d,]+\.\d{2})",
        r"(?:Total|Amount Paid|Final Total)[\s:\$]*\$?([\d,]+\.\d{2})",
    ]
    for pat in patterns:
        matches = re.findall(pat, text, re.IGNORECASE)
        if matches:
            try:
                amount = float(matches[0].replace(",", ""))
                break
            except ValueError:
                pass

    # Rule B: Unambiguous single-amount check (exactly one dollar amount in document)
    if amount is None:
        all_dollars_raw = re.findall(r"\$\s*([0-9,]+\.\d{2})", text)
        all_dollars: List[float] = []
        for d_str in all_dollars_raw:
            try:
                val = float(d_str.replace(",", ""))
                if val > 0:
                    all_dollars.append(val)
            except ValueError:
                pass

        if len(all_dollars) == 1:
            amount = all_dollars[0]
        else:
            amount = prompt_for_amount(filename, all_dollars, interactive=interactive)

    # Rule C: Ambiguous or missing amount -> warn and skip
    if amount is None:
        print(f"Warning: Could not extract purchase total from '{filename}'. Skipping file.")
        return None

    desc = f"{merchant} Purchase ({date_cand})"
    return date_cand, merchant, amount, desc


def load_purchase_ledger(ledger_path: str) -> Dict[str, PurchaseRecord]:
    """Reads human-editable purchase ledger CSV into record dictionary keyed by filename."""
    records: Dict[str, PurchaseRecord] = {}
    if not os.path.isfile(ledger_path):
        return records

    try:
        with open(ledger_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                fname = (row.get("filename") or "").strip()
                if not fname:
                    continue
                try:
                    amt = float(row.get("amount") or 0.0)
                except ValueError:
                    amt = 0.0
                records[fname] = PurchaseRecord(
                    date=(row.get("date") or "").strip(),
                    merchant=(row.get("merchant") or "").strip(),
                    amount=amt,
                    description=(row.get("description") or "").strip(),
                    filename=fname,
                    sha256=(row.get("sha256") or "").strip(),
                )
    except Exception as e:
        print(f"Warning: Could not read purchase ledger at '{ledger_path}': {e}")

    return records


def load_purchase_cache(cache_path: str) -> Dict[str, Any]:
    """Loads purchase cache JSON."""
    if not os.path.isfile(cache_path):
        return {"files": {}, "total_invested": 0.0}
    try:
        with open(cache_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: Could not read purchase cache at '{cache_path}': {e}")
        return {"files": {}, "total_invested": 0.0}


def save_purchase_ledger(ledger_path: str, records: List[PurchaseRecord]) -> None:
    """Writes sorted purchase records to human-readable CSV ledger."""
    os.makedirs(os.path.dirname(ledger_path) or ".", exist_ok=True)
    fieldnames = ["date", "merchant", "amount", "description", "filename", "sha256"]
    records_sorted = sorted(records, key=lambda r: (r.date, r.filename))
    with open(ledger_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in records_sorted:
            writer.writerow(asdict(r))


def save_purchase_cache(cache_path: str, cache_data: Dict[str, Any]) -> None:
    """Saves purchase cache JSON atomically."""
    os.makedirs(os.path.dirname(cache_path) or ".", exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(cache_data, f, indent=2)


def sync_purchase_history(
    purchase_dir: Optional[str],
    cache_path: Optional[str] = None,
    ledger_path: Optional[str] = None,
    seed_records: Optional[Dict[str, Dict[str, Any]]] = None,
    interactive: Optional[bool] = None,
) -> Tuple[float, List[PurchaseRecord]]:
    """
    Synchronizes purchase documents against SHA-256 cache and CSV ledger:
    1. Scans purchase_dir for receipt files (.pdf, .csv, .txt, .json).
    2. Compares SHA-256 checksums to avoid re-parsing cached files.
    3. Re-uses cached records or loads explicit ledger overrides.
    4. Automatically parses new receipts or checks seed_records.
    5. Updates cache and CSV ledger.
    Returns (total_invested_amount, list_of_records).
    """
    if not purchase_dir or not os.path.isdir(purchase_dir):
        # If directory doesn't exist, check if an existing ledger or cache exists
        if ledger_path and os.path.isfile(ledger_path):
            existing_ledger = load_purchase_ledger(ledger_path)
            if existing_ledger:
                records = list(existing_ledger.values())
                total = round(sum(r.amount for r in records), 2)
                return total, records
        if cache_path and os.path.isfile(cache_path):
            cache_data = load_purchase_cache(cache_path)
            cached_files = cache_data.get("files", {})
            records = [PurchaseRecord(**v) for v in cached_files.values() if isinstance(v, dict)]
            total = round(sum(r.amount for r in records), 2)
            return total, records
        return 0.0, []

    if not cache_path:
        cache_path = os.path.join(purchase_dir, "purchase_history_cache.json")
    if not ledger_path:
        ledger_path = os.path.join(purchase_dir, "purchase_history.csv")

    cache_data = load_purchase_cache(cache_path)
    cached_files = cache_data.get("files", {})
    existing_ledger = load_purchase_ledger(ledger_path)
    seed_dict = seed_records or {}

    discovered_records: Dict[str, PurchaseRecord] = {}
    unparsed_files: List[str] = []

    files = [
        f for f in Path(purchase_dir).iterdir()
        if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    for fpath in files:
        fname = fpath.name
        file_sha256 = compute_file_sha256(str(fpath))

        # Check 1: Cache match (same filename and sha256)
        if fname in cached_files and cached_files[fname].get("sha256") == file_sha256:
            cached_entry = cached_files[fname]
            discovered_records[fname] = PurchaseRecord(
                date=cached_entry.get("date", ""),
                merchant=cached_entry.get("merchant", "Unknown"),
                amount=float(cached_entry.get("amount", 0.0)),
                description=cached_entry.get("description", ""),
                filename=fname,
                sha256=file_sha256,
            )
            continue

        # Check 2: Ledger override (user edited purchase_history.csv)
        if fname in existing_ledger and existing_ledger[fname].amount > 0:
            rec = existing_ledger[fname]
            rec.sha256 = file_sha256
            discovered_records[fname] = rec
            continue

        # Check 3: Pre-seeded records
        if fname in seed_dict:
            s = seed_dict[fname]
            discovered_records[fname] = PurchaseRecord(
                date=str(s.get("date", "")),
                merchant=str(s.get("merchant", "Unknown")),
                amount=float(s.get("amount", 0.0)),
                description=str(s.get("description", "")),
                filename=fname,
                sha256=file_sha256,
            )
            continue

        # Check 4: Automated document parsing for supported formats
        parsed = parse_receipt_document(str(fpath), interactive=interactive)
        if parsed:
            p_date, p_merchant, p_amount, p_desc = parsed
            discovered_records[fname] = PurchaseRecord(
                date=p_date,
                merchant=p_merchant,
                amount=p_amount,
                description=p_desc,
                filename=fname,
                sha256=file_sha256,
            )
        else:
            unparsed_files.append(fname)

    if unparsed_files:
        print(f"Skipped {len(unparsed_files)} unparseable or bad receipt file(s): {', '.join(unparsed_files[:5])}")

    all_records = list(discovered_records.values())
    total_invested = round(sum(r.amount for r in all_records), 2)

    # Save cache
    new_cache = {
        "files": {r.filename: asdict(r) for r in all_records},
        "total_invested": total_invested,
        "last_updated": datetime.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S"),
    }
    save_purchase_cache(cache_path, new_cache)

    # Save ledger CSV
    save_purchase_ledger(ledger_path, all_records)

    return total_invested, all_records
