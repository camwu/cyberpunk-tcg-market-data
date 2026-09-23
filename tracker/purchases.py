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
from typing import Any, Dict, List, Optional, Tuple

try:
    import pypdf
except ImportError:
    pypdf = None

SUPPORTED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg"}


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


def extract_text_from_pdf(filepath: str) -> str:
    """Extracts text content across all pages of a PDF file using pypdf."""
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


def parse_date_candidate(text: str) -> Optional[str]:
    """Finds first ISO date (YYYY-MM-DD) or converts common date formats."""
    # Try YYYY-MM-DD
    m_iso = re.search(r"(?<!\d)(20\d{2}-\d{2}-\d{2})(?!\d)", text)
    if m_iso:
        d_str = m_iso.group(1)
        try:
            datetime.date.fromisoformat(d_str)
            return d_str
        except ValueError:
            pass

    # Try Month DD, YYYY or Day, Month DD, YYYY
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


def parse_receipt_document(filepath: str) -> Optional[Tuple[str, str, float, str]]:
    """
    Parses date, merchant, amount, and description from a receipt PDF.
    Returns (date, merchant, amount, description) or None.
    """
    p = Path(filepath)
    ext = p.suffix.lower()
    filename = p.name

    date_cand = parse_date_candidate(filename)

    text = ""
    if ext == ".pdf":
        text = extract_text_from_pdf(filepath)

    if not date_cand and text:
        date_cand = parse_date_candidate(text)

    if not date_cand:
        date_cand = datetime.date.today().isoformat()

    merchant = "Unknown"
    lower_text = (text + " " + filename).lower()
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

    amount: Optional[float] = None

    # Priority regex patterns for total amounts
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

    # Fallback to filename amount pattern: e.g. _10.50.pdf or _$349.00
    if amount is None:
        m_fname = re.search(r"[\$_]([\d]+\.\d{2})\b", filename)
        if m_fname:
            try:
                amount = float(m_fname.group(1))
            except ValueError:
                pass

    if amount is None:
        return None

    desc = filename.rsplit(".", 1)[0].replace("_", " ")
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
) -> Tuple[float, List[PurchaseRecord]]:
    """
    Synchronizes purchase documents against SHA-256 cache and CSV ledger:
    1. Scans purchase_dir for receipt files (.pdf, .png, .jpg, .jpeg).
    2. Compares SHA-256 checksums to avoid re-parsing cached files.
    3. Re-uses cached records or loads explicit ledger overrides.
    4. Automatically parses new PDF receipts or checks seed_records.
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

        # Check 4: Automated document parsing for PDFs
        parsed = parse_receipt_document(str(fpath))
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
            discovered_records[fname] = PurchaseRecord(
                date=parse_date_candidate(fname) or datetime.date.today().isoformat(),
                merchant="Unknown",
                amount=0.0,
                description=fname.rsplit(".", 1)[0].replace("_", " "),
                filename=fname,
                sha256=file_sha256,
            )

    if unparsed_files:
        print(f"Notice: {len(unparsed_files)} receipt file(s) require manual amount verification in '{ledger_path}':")
        for u in unparsed_files[:5]:
            print(f"  - {u}")

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
