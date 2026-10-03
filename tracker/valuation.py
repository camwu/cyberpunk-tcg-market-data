"""
Valuation engine for Cyberpunk TCG collections.
Matches inventory records against TCGplayer market prices, computes rolling metrics,
and persists historical daily snapshots into a local SQLite database.
"""

from dataclasses import dataclass
import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
from typing import Dict, Any, List, Optional, Tuple, Set

from tracker.validation import (
    validate_collection_file,
    validate_sealed_file,
    format_validation_report,
    is_sealed_product,
    extract_date_from_text,
    CollectionValidationError,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
REPO_PRICES_DIR = REPO_ROOT / "prices"


@dataclass
class ItemValuationContext:
    card_key: str
    item_type: str
    prod_id: Optional[int]
    name: str
    print_number: Optional[str]
    expansion: str
    finish: str
    rarity: Optional[str]
    color: Optional[str]
    card_type: Optional[str]
    effective_acq_date: str
    fallback_price: float
    qty: int


def sanitize_card_name(name: str) -> str:
    """Strips upstream disambiguation suffixes (e.g. (Epic), (Secret), (IO), (a)) from catalog card names."""
    if not name:
        return ""
    return re.sub(r"\s*\((?:Epic|Rare|Secret|Iconic|ILegend|IO|ISecret|007|[ab])\)$", "", name.strip(), flags=re.IGNORECASE)


def get_earliest_price_date(cache_dir: str, cur: Optional[sqlite3.Cursor] = None) -> Optional[str]:
    """Finds the earliest available price date across price cache files and database snapshots."""
    dates = []
    candidates = [cache_dir]
    repo_prices = str(REPO_PRICES_DIR)
    if repo_prices not in candidates:
        candidates.append(repo_prices)

    for c_dir in candidates:
        if c_dir and os.path.isdir(c_dir):
            for f in os.listdir(c_dir):
                if f.endswith(".json") and f != "latest.json":
                    stem = f[:-5]
                    try:
                        datetime.date.fromisoformat(stem)
                        dates.append(stem)
                    except ValueError:
                        pass
    if cur:
        try:
            cur.execute("SELECT MIN(date) FROM daily_snapshots")
            db_min = cur.fetchone()[0]
            if db_min:
                dates.append(db_min)
        except sqlite3.Error:
            pass
    return min(dates) if dates else None


def get_historical_market_price(
    cache_dir: str,
    target_date: str,
    prod_id: Optional[int],
    expansion: str,
    print_number: Optional[str],
    name: str,
    finish: str,
    price_cache: Optional[Dict[Tuple[str, str], Any]] = None,
) -> Optional[float]:
    """Retrieves market price for a product on target_date from price cache files."""
    cache_key = (os.path.abspath(cache_dir), target_date)
    cache_data = None
    if price_cache is not None and cache_key in price_cache:
        cache_data = price_cache[cache_key]

    if cache_data is None:
        candidates = [
            os.path.join(cache_dir, f"{target_date}.json"),
            str(REPO_PRICES_DIR / f"{target_date}.json"),
        ]
        target_file = next((c for c in candidates if os.path.isfile(c)), None)
        if not target_file:
            loaded_data = {}
        else:
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    loaded_data = json.load(f)
            except (OSError, json.JSONDecodeError) as e:
                print(f"Warning: Could not read historical price file '{target_file}': {e}", file=sys.stderr)
                loaded_data = {}

        if price_cache is not None:
            price_cache[cache_key] = loaded_data
        cache_data = loaded_data

    if not cache_data:
        return None

    sub_type = "Foil" if finish.lower() == "foil" else "Normal"

    def _extract_from_price_dict(p_dict: dict) -> Optional[float]:
        if not isinstance(p_dict, dict):
            return None
        finish_keys = ["Foil"] if sub_type == "Foil" else ["Normal", "Standard"]
        for k in finish_keys:
            sub = p_dict.get(k)
            if isinstance(sub, dict):
                mp = sub.get("marketPrice") or sub.get("midPrice") or sub.get("lowPrice")
                if mp is not None and mp > 0.0:
                    return float(mp)
        # Check remaining sub-dictionaries as fallback
        for k, sub in p_dict.items():
            if isinstance(sub, dict) and k not in finish_keys:
                mp = sub.get("marketPrice") or sub.get("midPrice") or sub.get("lowPrice")
                if mp is not None and mp > 0.0:
                    return float(mp)
        return None

    if "products" in cache_data:
        for p in cache_data["products"].values():
            p_pid = p.get("productId")
            p_exp = (p.get("groupName") or "").strip().lower()
            p_name = (p.get("name") or "").strip().lower()
            p_pnum = (p.get("printNumber") or "").strip().lower()
            match = False
            if prod_id and p_pid and int(p_pid) == int(prod_id):
                match = True
            elif print_number and p_exp == expansion.lower() and p_pnum == str(print_number).strip().lower():
                match = True
            elif not print_number and p_exp == expansion.lower() and p_name == name.lower():
                match = True

            if match:
                price = _extract_from_price_dict(p.get("prices", {}))
                if price is not None:
                    return price

    elif "prices" in cache_data and prod_id:
        p_data = cache_data["prices"].get(str(prod_id)) or cache_data["prices"].get(int(prod_id))
        if isinstance(p_data, dict):
            price = _extract_from_price_dict(p_data)
            if price is not None:
                return price

    return None


def init_database(db_path: str) -> sqlite3.Connection:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS card_metadata (
        card_key TEXT PRIMARY KEY,
        product_id INTEGER,
        name TEXT,
        print_number TEXT,
        expansion TEXT,
        finish TEXT,
        rarity TEXT,
        color TEXT,
        first_seen_date TEXT,
        baseline_market_price REAL
    )
    """)

    cur.execute("PRAGMA table_info(card_metadata)")
    cols = [col[1] for col in cur.fetchall()]
    if "color" not in cols:
        cur.execute("ALTER TABLE card_metadata ADD COLUMN color TEXT")
    if "item_type" not in cols:
        cur.execute("ALTER TABLE card_metadata ADD COLUMN item_type TEXT DEFAULT 'Card'")
    if "card_type" not in cols:
        cur.execute("ALTER TABLE card_metadata ADD COLUMN card_type TEXT")

    # Migrate legacy 3-part sealed keys (SEALED::{expansion}::{productId}) to lot keys with acquisitionDate
    cur.execute("SELECT card_key, first_seen_date FROM card_metadata WHERE item_type = 'Sealed'")
    for old_key, first_seen in cur.fetchall():
        parts = old_key.split("::")
        if len(parts) == 3:
            if not first_seen:
                cur.execute("SELECT MIN(date) FROM daily_snapshots WHERE card_key = ?", (old_key,))
                min_row = cur.fetchone()
                migrated_date = min_row[0] if (min_row and min_row[0]) else datetime.date.today().isoformat()
            else:
                migrated_date = first_seen
            new_key = f"{old_key}::{migrated_date}"
            cur.execute("UPDATE card_metadata SET card_key = ?, first_seen_date = COALESCE(first_seen_date, ?) WHERE card_key = ?", (new_key, migrated_date, old_key))
            cur.execute("UPDATE daily_snapshots SET card_key = ? WHERE card_key = ?", (new_key, old_key))

    # Migrate legacy 3-part card keys ({expansion}::{print_number}::{finish}) to lot keys with acquisitionDate
    cur.execute("SELECT card_key, first_seen_date FROM card_metadata WHERE COALESCE(item_type, 'Card') = 'Card'")
    for old_key, first_seen in cur.fetchall():
        parts = old_key.split("::")
        if len(parts) == 3:
            if not first_seen:
                cur.execute("SELECT MIN(date) FROM daily_snapshots WHERE card_key = ?", (old_key,))
                min_row = cur.fetchone()
                migrated_date = min_row[0] if (min_row and min_row[0]) else datetime.date.today().isoformat()
            else:
                migrated_date = first_seen
            new_key = f"{old_key}::{migrated_date}"
            cur.execute("UPDATE card_metadata SET card_key = ?, first_seen_date = COALESCE(first_seen_date, ?) WHERE card_key = ?", (new_key, migrated_date, old_key))
            cur.execute("UPDATE daily_snapshots SET card_key = ? WHERE card_key = ?", (new_key, old_key))

    cur.execute("""
    CREATE TABLE IF NOT EXISTS daily_snapshots (
        date TEXT,
        card_key TEXT,
        quantity INTEGER,
        unit_market_price REAL,
        unit_low_price REAL,
        unit_mid_price REAL,
        unit_high_price REAL,
        line_total REAL,
        baseline_price REAL,
        lifetime_gain_dollar REAL,
        lifetime_gain_pct REAL,
        PRIMARY KEY (date, card_key)
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS portfolio_daily_summary (
        date TEXT PRIMARY KEY,
        total_value REAL,
        total_cards INTEGER,
        unique_items INTEGER,
        l7d_dollar_delta REAL,
        l7d_pct_delta REAL,
        lifetime_dollar_gain REAL,
        lifetime_pct_gain REAL,
        total_sealed INTEGER DEFAULT 0,
        collection_updated_at TEXT,
        collection_source TEXT
    )
    """)

    cur.execute("PRAGMA table_info(portfolio_daily_summary)")
    sum_cols = [col[1] for col in cur.fetchall()]
    if "total_sealed" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN total_sealed INTEGER DEFAULT 0")
    if "collection_updated_at" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN collection_updated_at TEXT")
    if "collection_source" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN collection_source TEXT")
    if "total_cost_basis" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN total_cost_basis REAL DEFAULT 0.0")
    if "net_unrealized_gain" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN net_unrealized_gain REAL DEFAULT 0.0")
    if "net_unrealized_pct" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN net_unrealized_pct REAL DEFAULT 0.0")
    if "purchases_updated_at" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN purchases_updated_at TEXT")

    conn.commit()
    return conn


def load_price_catalog(
    cache_dir: str,
    date_str: str,
    price_file: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Loads price cache JSON and constructs catalog lookup indexes."""
    if price_file and os.path.isfile(price_file):
        cache_file = price_file
    else:
        candidates = [
            os.path.join(cache_dir, f"{date_str}.json"),
            str(REPO_PRICES_DIR / f"{date_str}.json"),
        ]
        cache_file = next((c for c in candidates if os.path.isfile(c)), None)
        if not cache_file:
            raise FileNotFoundError(f"Price cache file not found for {date_str} in candidates: {candidates}")

    with open(cache_file, "r", encoding="utf-8") as f:
        cache_data = json.load(f)

    if "products" in cache_data:
        prods = cache_data.get("products", {})
    elif "prices" in cache_data:
        cards_candidates = [
            os.path.join(cache_dir, "cards.json"),
            os.path.join(os.path.dirname(cache_dir), "cards.json"),
            str(REPO_ROOT / "cards.json"),
        ]
        cards_file = next((c for c in cards_candidates if os.path.isfile(c)), None)
        cards_catalog = {}
        if cards_file:
            with open(cards_file, "r", encoding="utf-8") as cf:
                cards_catalog = json.load(cf)

        daily_prices = cache_data.get("prices", {})
        prods = {}
        for pid_str, card_meta in cards_catalog.items():
            if str(pid_str).startswith("_"):
                continue
            card_dict = dict(card_meta)
            card_dict["prices"] = daily_prices.get(pid_str, {})
            prods[pid_str] = card_dict
        for pid_str, p_data in daily_prices.items():
            if pid_str not in prods:
                prods[pid_str] = {
                    "productId": int(pid_str) if pid_str.isdigit() else pid_str,
                    "prices": p_data,
                }
    else:
        prods = {}

    catalog_by_group_pnum = {}
    catalog_by_pid = {}
    catalog_by_group_name = {}
    catalog_by_group_clean_name = {}
    catalog_by_name = {}
    for p in prods.values():
        pid = p.get("productId")
        if pid:
            catalog_by_pid[int(pid)] = p
            catalog_by_pid[str(pid)] = p
        g = p.get("groupName", "").strip().lower()
        p_name = p.get("name", "").strip().lower()
        p_clean = p.get("cleanName", "").strip().lower()
        pnum = p.get("printNumber")
        if not pnum and isinstance(p.get("extendedData"), dict):
            pnum = p.get("extendedData", {}).get("number")
        elif not pnum and isinstance(p.get("extendedData"), list):
            for ext in p.get("extendedData", []):
                if ext.get("name") == "Number":
                    pnum = ext.get("value")
                    break
        clean_name = p.get("cleanName", "")
        if not pnum:
            for part in clean_name.split():
                if any(c.isdigit() for c in part) and any(c.isalpha() for c in part):
                    pnum = part
                    break
        if not pnum:
            name_parts = p.get("name", "").split()
            for part in name_parts:
                if any(c.isdigit() for c in part) and any(c.isalpha() for c in part):
                    pnum = part
                    break
        if g and pnum:
            catalog_by_group_pnum[(g, str(pnum).strip().lower())] = p
        if g and p_name:
            catalog_by_group_name[(g, p_name)] = p
        if g and p_clean:
            catalog_by_group_clean_name[(g, p_clean)] = p
        if p_name:
            catalog_by_name[p_name] = p

    indexes = {
        "by_group_pnum": catalog_by_group_pnum,
        "by_pid": catalog_by_pid,
        "by_group_name": catalog_by_group_name,
        "by_group_clean_name": catalog_by_group_clean_name,
        "by_name": catalog_by_name,
    }
    return prods, indexes


def deduplicate_and_merge_items(
    collection_rows: Optional[List[Dict[str, Any]]],
    sealed_rows: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Merges collection rows with sealed inventory, eliminating duplicates."""
    card_items = list(collection_rows or [])
    sealed_items = list(sealed_rows or [])

    if sealed_items:
        card_item_identifiers = {
            (r.get("name", "").strip().lower(), r.get("expansion", "").strip().lower())
            for r in card_items
            if r.get("item_type") == "Sealed" or is_sealed_product(r.get("name", ""), r.get("expansion", ""))
        }
        sealed_deduped = [
            s for s in sealed_items
            if (s.get("name", "").strip().lower(), s.get("expansion", "").strip().lower()) not in card_item_identifiers
        ]
        all_items = card_items + sealed_deduped
    else:
        all_items = card_items

    return card_items, sealed_items, all_items


def extract_raw_acquisition_date(row: Dict[str, Any]) -> Optional[str]:
    """Extracts raw acquisition date from row's acquisitionDate or notes field."""
    raw = (row.get("acquisitionDate") or "").strip()
    if not raw and row.get("notes"):
        raw = extract_date_from_text(row.get("notes")) or ""
    return raw or None


def lookup_first_seen_date(
    cur: sqlite3.Cursor,
    item_type: str,
    prod_id: Optional[int],
    name: str,
    expansion: str,
    print_number: Optional[str],
    finish: str,
) -> Optional[str]:
    """Queries card_metadata for the earliest first_seen_date matching the item."""
    if item_type == "Sealed":
        cur.execute(
            "SELECT card_key, first_seen_date FROM card_metadata WHERE item_type = 'Sealed' AND (product_id = ? OR name = ?) ORDER BY first_seen_date ASC LIMIT 1",
            (prod_id, name),
        )
    else:
        cur.execute(
            "SELECT card_key, first_seen_date FROM card_metadata WHERE item_type = 'Card' AND expansion = ? AND print_number = ? AND finish = ? ORDER BY first_seen_date ASC LIMIT 1",
            (expansion, print_number, finish),
        )
    existing_meta = cur.fetchone()
    if existing_meta and existing_meta[1]:
        return existing_meta[1]
    return None


def match_collection_item(
    row: Dict[str, Any],
    indexes: Dict[str, Any],
    effective_acq_date: str,
) -> Tuple[Optional[Dict[str, Any]], ItemValuationContext, Tuple, bool, str]:
    """Pure function matching a collection row against catalog indexes and constructing ItemValuationContext."""
    item_type = row.get("item_type")
    if not item_type:
        item_type = "Sealed" if is_sealed_product(row.get("name", ""), row.get("expansion", "")) else "Card"

    name = row["name"].strip()
    expansion = row["expansion"].strip()
    print_number = (row.get("printNumber") or "").strip() or None
    finish = (row.get("finish") or "Standard").strip()
    qty = int(row.get("totalQtyOwned", 1))
    fallback_price = float(row.get("price") or 0.0)
    row_pid = row.get("productId")

    prod = None
    if print_number:
        prod = indexes["by_group_pnum"].get((expansion.lower(), print_number.lower()))
    if not prod:
        prod = indexes["by_group_name"].get((expansion.lower(), name.lower()))
    if not prod:
        prod = indexes["by_group_clean_name"].get((expansion.lower(), name.lower()))
    if not prod:
        prod = indexes["by_name"].get(name.lower())
    if not prod and row_pid:
        prod = indexes["by_pid"].get(int(row_pid)) or indexes["by_pid"].get(str(row_pid))

    prod_id = prod["productId"] if prod else (int(row_pid) if row_pid else None)
    distinct_key = (item_type, expansion.lower(), (print_number or name).lower(), finish.lower())

    if prod:
        name = sanitize_card_name(prod.get("name") or name)
        expansion = prod.get("groupName") or expansion
        rarity = prod.get("rarity") or row.get("rarity")
        color = (prod.get("color") or row.get("color") or "").strip()
        card_type = prod.get("cardType") or row.get("card_type")
        is_matched = True
        label = f"'{name}' ({print_number or 'N/A'}, {expansion})"
    else:
        label = f"'{name}' ({print_number or 'N/A'}, {expansion})"
        name = sanitize_card_name(name)
        rarity = row.get("rarity")
        color = (row.get("color") or "").strip()
        card_type = row.get("card_type")
        is_matched = False

    if item_type == "Sealed":
        card_key = f"SEALED::{expansion}::{prod_id or name}::{effective_acq_date}"
        rarity = "Sealed"
    else:
        card_key = f"{expansion}::{print_number}::{finish}::{effective_acq_date}"

    ctx = ItemValuationContext(
        card_key=card_key,
        item_type=item_type,
        prod_id=prod_id,
        name=name,
        print_number=print_number,
        expansion=expansion,
        finish=finish,
        rarity=rarity,
        color=color,
        card_type=card_type,
        effective_acq_date=effective_acq_date,
        fallback_price=fallback_price,
        qty=qty,
    )
    return prod, ctx, distinct_key, is_matched, label


def resolve_item_market_price(
    prod: Optional[Dict[str, Any]],
    ctx: ItemValuationContext,
    date_str: str,
    cur: sqlite3.Cursor,
) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
    """Resolves market price across finish tiers, historical snapshots, and CSV fallback."""
    sub_type = "Foil" if ctx.finish.lower() == "foil" else "Normal"
    prices = prod.get("prices", {}).get(sub_type, {}) if prod else {}

    market_price = prices.get("marketPrice")
    if market_price is None:
        market_price = prices.get("midPrice")
    if market_price is None:
        market_price = prices.get("lowPrice")

    if market_price is None and prod and prod.get("prices"):
        other_sub = "Normal" if sub_type == "Foil" else "Foil"
        other_prices = prod.get("prices", {}).get(other_sub, {})
        market_price = other_prices.get("marketPrice") or other_prices.get("midPrice") or other_prices.get("lowPrice")
        if market_price is None:
            for p_dict in prod.get("prices", {}).values():
                if isinstance(p_dict, dict):
                    market_price = p_dict.get("marketPrice") or p_dict.get("midPrice") or p_dict.get("lowPrice")
                    if market_price is not None:
                        break

    if market_price is None or market_price <= 0.0:
        cur.execute("""
        SELECT s.unit_market_price
        FROM daily_snapshots s
        JOIN card_metadata m ON s.card_key = m.card_key
        WHERE s.date < ? AND s.unit_market_price > 0.0
          AND ((m.product_id IS NOT NULL AND m.product_id = ?)
               OR (m.expansion = ? AND m.print_number = ? AND m.finish = ?)
               OR (m.expansion = ? AND m.name = ?))
        ORDER BY s.date DESC
        LIMIT 1
        """, (date_str, ctx.prod_id, ctx.expansion, ctx.print_number, ctx.finish, ctx.expansion, ctx.name))
        prev_price_row = cur.fetchone()
        if prev_price_row and prev_price_row[0] is not None and prev_price_row[0] > 0.0:
            market_price = prev_price_row[0]
        elif ctx.fallback_price > 0.0:
            market_price = ctx.fallback_price
        else:
            market_price = None

    if market_price is not None and market_price > 0.0:
        unit_low = prices.get("lowPrice") or market_price
        unit_mid = prices.get("midPrice") or market_price
        unit_high = prices.get("highPrice") or market_price
        line_total = round(ctx.qty * market_price, 2)
    else:
        market_price = None
        unit_low = None
        unit_mid = None
        unit_high = None
        line_total = None

    return market_price, unit_low, unit_mid, unit_high, line_total


def resolve_and_persist_baseline(
    cur: sqlite3.Cursor,
    ctx: ItemValuationContext,
    date_str: str,
    market_price: Optional[float],
    cache_dir: str,
    price_cache: Optional[Dict[Tuple[str, str], Any]] = None,
) -> Optional[float]:
    """Resolves item baseline price, updating existing card metadata or inserting a new record."""
    cur.execute("SELECT baseline_market_price, color, item_type, name, rarity, card_type FROM card_metadata WHERE card_key = ?", (ctx.card_key,))
    meta_res = cur.fetchone()
    if meta_res:
        baseline_price = meta_res[0]
        existing_color = meta_res[1]
        existing_item_type = meta_res[2] if len(meta_res) > 2 else "Card"
        existing_name = meta_res[3] if len(meta_res) > 3 else None
        existing_rarity = meta_res[4] if len(meta_res) > 4 else None
        existing_card_type = meta_res[5] if len(meta_res) > 5 else None
        if ctx.color and existing_color != ctx.color:
            cur.execute("UPDATE card_metadata SET color = ? WHERE card_key = ?", (ctx.color, ctx.card_key))
        if ctx.name and existing_name != ctx.name:
            cur.execute("UPDATE card_metadata SET name = ? WHERE card_key = ?", (ctx.name, ctx.card_key))
        if ctx.rarity and existing_rarity != ctx.rarity:
            cur.execute("UPDATE card_metadata SET rarity = ? WHERE card_key = ?", (ctx.rarity, ctx.card_key))
        if not existing_item_type or existing_item_type != ctx.item_type:
            cur.execute("UPDATE card_metadata SET item_type = ? WHERE card_key = ?", (ctx.item_type, ctx.card_key))
        if ctx.card_type and existing_card_type != ctx.card_type:
            cur.execute("UPDATE card_metadata SET card_type = ? WHERE card_key = ?", (ctx.card_type, ctx.card_key))
        if baseline_price is None or baseline_price <= 0.0:
            if ctx.fallback_price > 0.0:
                baseline_price = ctx.fallback_price
            elif market_price is not None and market_price > 0.0:
                baseline_price = market_price
            if baseline_price and baseline_price > 0.0:
                cur.execute("UPDATE card_metadata SET baseline_market_price = ? WHERE card_key = ?", (baseline_price, ctx.card_key))
    else:
        if ctx.item_type == "Sealed" and ctx.fallback_price > 0.0:
            baseline_price = ctx.fallback_price
        else:
            baseline_price = None
            if ctx.effective_acq_date and ctx.effective_acq_date < date_str:
                hist_price = get_historical_market_price(
                    cache_dir=cache_dir,
                    target_date=ctx.effective_acq_date,
                    prod_id=ctx.prod_id,
                    expansion=ctx.expansion,
                    print_number=ctx.print_number,
                    name=ctx.name,
                    finish=ctx.finish,
                    price_cache=price_cache,
                )
                if hist_price is not None and hist_price > 0.0:
                    baseline_price = hist_price

            if baseline_price is None or baseline_price <= 0.0:
                if market_price is not None and market_price > 0.0:
                    baseline_price = market_price
                elif ctx.fallback_price > 0.0:
                    baseline_price = ctx.fallback_price
                else:
                    baseline_price = None

        first_seen = ctx.effective_acq_date
        cur.execute("""
        INSERT INTO card_metadata (card_key, product_id, name, print_number, expansion, finish, rarity, color, first_seen_date, baseline_market_price, item_type, card_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ctx.card_key, ctx.prod_id, ctx.name, ctx.print_number, ctx.expansion, ctx.finish, ctx.rarity, ctx.color, first_seen, baseline_price, ctx.item_type, ctx.card_type))

    return baseline_price


def calculate_l7d_metrics(cur: sqlite3.Cursor, date_str: str) -> Tuple[float, float]:
    """Calculates rolling 7-day dollar and percentage deltas from historical snapshots."""
    cur.execute("""
    SELECT date
    FROM portfolio_daily_summary
    WHERE date < ?
    ORDER BY date DESC
    LIMIT 7
    """, (date_str,))
    history_dates = cur.fetchall()

    if not history_dates:
        return 0.0, 0.0

    l7d_target_date = history_dates[-1][0]
    cur.execute("""
    SELECT s.card_key, s.quantity, s.unit_market_price, s.baseline_price, prev.unit_market_price
    FROM daily_snapshots s
    LEFT JOIN daily_snapshots prev ON s.card_key = prev.card_key AND prev.date = ?
    WHERE s.date = ? AND s.unit_market_price IS NOT NULL
    """, (l7d_target_date, date_str))

    l7d_dollar_delta = 0.0
    l7d_baseline_total = 0.0
    for ckey, qty, curr_p, base_p, prev_p in cur.fetchall():
        ref_p = prev_p if (prev_p is not None and prev_p > 0.0) else base_p
        if ref_p is not None and ref_p > 0.0:
            l7d_dollar_delta += (curr_p - ref_p) * qty
            l7d_baseline_total += ref_p * qty
        elif curr_p is not None and curr_p > 0.0:
            l7d_baseline_total += curr_p * qty

    l7d_dollar_delta = round(l7d_dollar_delta, 2)
    l7d_pct_delta = round((l7d_dollar_delta / l7d_baseline_total) * 100.0, 2) if l7d_baseline_total > 0 else 0.0
    return l7d_dollar_delta, l7d_pct_delta


def persist_portfolio_summary(
    cur: sqlite3.Cursor,
    conn: sqlite3.Connection,
    date_str: str,
    total_value: float,
    total_cards: int,
    unique_items: int,
    l7d_dollar_delta: float,
    l7d_pct_delta: float,
    total_lifetime_gain: float,
    lifetime_pct_gain: float,
    total_sealed: int,
    collection_updated_at: Optional[str],
    collection_source: Optional[str],
    total_cost_basis: float,
    net_unrealized_gain: float,
    net_unrealized_pct: float,
    purchases_updated_at: Optional[str],
) -> None:
    """Inserts or replaces record into portfolio_daily_summary and commits."""
    cur.execute("""
    INSERT OR REPLACE INTO portfolio_daily_summary (
        date, total_value, total_cards, unique_items,
        l7d_dollar_delta, l7d_pct_delta, lifetime_dollar_gain, lifetime_pct_gain,
        total_sealed, collection_updated_at, collection_source,
        total_cost_basis, net_unrealized_gain, net_unrealized_pct,
        purchases_updated_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        date_str, total_value, total_cards, unique_items,
        l7d_dollar_delta, l7d_pct_delta, total_lifetime_gain, lifetime_pct_gain,
        total_sealed, collection_updated_at, collection_source,
        total_cost_basis, net_unrealized_gain, net_unrealized_pct,
        purchases_updated_at
    ))
    conn.commit()


def calculate_portfolio_valuation(
    date_str: str,
    collection_path: str,
    cache_dir: str,
    db_path: str,
    force: bool = False,
    collection_rows: Optional[List[Dict[str, Any]]] = None,
    sealed_path: Optional[str] = None,
    sealed_rows: Optional[List[Dict[str, Any]]] = None,
    price_file: Optional[str] = None,
    collection_updated_at: Optional[str] = None,
    collection_source: Optional[str] = None,
    total_cost_basis: Optional[float] = None,
    purchases_updated_at: Optional[str] = None,
) -> Dict[str, Any]:
    """Orchestrates collection ingestion, price lookup, baseline resolution, and database snapshotting."""
    if collection_rows is None:
        is_valid, validation_errors, collection_rows = validate_collection_file(collection_path)
        if not is_valid:
            error_msg = f"Collection validation failed for '{collection_path}':\n" + format_validation_report(validation_errors)
            raise CollectionValidationError(error_msg, errors=validation_errors)

    if sealed_rows is None and sealed_path and os.path.exists(sealed_path):
        is_valid_sealed, sealed_errors, sealed_rows = validate_sealed_file(sealed_path)
        if not is_valid_sealed:
            error_msg = f"Sealed inventory validation failed for '{sealed_path}':\n" + format_validation_report(sealed_errors)
            raise CollectionValidationError(error_msg, errors=sealed_errors)

    conn = init_database(db_path)
    cur = conn.cursor()

    if not force:
        cur.execute("SELECT COUNT(*) FROM portfolio_daily_summary WHERE date = ?", (date_str,))
        if cur.fetchone()[0] > 0:
            print(f"Snapshot for {date_str} already exists in database. Use force=True to overwrite.")
            cur.execute("""
            SELECT total_value, total_cards, unique_items, lifetime_dollar_gain, lifetime_pct_gain,
                   COALESCE(total_sealed, 0), COALESCE(total_cost_basis, 0.0),
                   COALESCE(net_unrealized_gain, 0.0), COALESCE(net_unrealized_pct, 0.0)
            FROM portfolio_daily_summary WHERE date = ?
            """, (date_str,))
            row = cur.fetchone()
            conn.close()
            return {
                "total_value": row[0],
                "total_cards": row[1],
                "unique_items": row[2],
                "lifetime_dollar_gain": row[3],
                "lifetime_pct_gain": row[4],
                "total_sealed": row[5] if len(row) > 5 else 0,
                "total_cost_basis": row[6] if len(row) > 6 else 0.0,
                "net_unrealized_gain": row[7] if len(row) > 7 else 0.0,
                "net_unrealized_pct": row[8] if len(row) > 8 else 0.0,
            }

    prods, catalog_indexes = load_price_catalog(cache_dir, date_str, price_file)
    earliest_price_date = get_earliest_price_date(cache_dir, cur)

    card_items, sealed_items, all_items = deduplicate_and_merge_items(collection_rows, sealed_rows)

    if prods:
        catalog_group_names = {
            (p.get("groupName") or "").strip().lower()
            for p in prods.values()
            if p.get("groupName")
        }
        if catalog_group_names:
            collection_expansions = {
                row["expansion"].strip()
                for row in all_items
                if row.get("expansion")
            }
            for exp in sorted(collection_expansions):
                if exp.lower() not in catalog_group_names:
                    print(f"Warning: Expansion '{exp}' not found in price catalog groups — cards from this expansion may fail price lookup.", flush=True)

    print(f"Calculating portfolio valuation for {date_str} across {len(card_items)} collection rows and {len(sealed_items)} legacy sealed items...")

    total_value = 0.0
    total_cards = 0
    total_sealed = 0
    unique_items = len(all_items)
    total_lifetime_gain = 0.0

    unmatched_item_labels: dict = {}
    zero_price_item_labels: dict = {}
    matched_distinct: set = set()
    seen_distinct: set = set()

    # Session-scoped historical price cache for get_historical_market_price calls
    session_price_cache: Dict[Tuple[str, str], Any] = {}

    for row in all_items:
        raw_acq_date = extract_raw_acquisition_date(row)
        if raw_acq_date:
            effective_acq_date = max(raw_acq_date, earliest_price_date) if earliest_price_date else raw_acq_date
        else:
            item_type = row.get("item_type") or ("Sealed" if is_sealed_product(row.get("name", ""), row.get("expansion", "")) else "Card")
            pnum = (row.get("printNumber") or "").strip() or None
            name_raw = row["name"].strip()
            exp_raw = row["expansion"].strip()
            finish_raw = (row.get("finish") or "Standard").strip()
            row_pid = row.get("productId")

            prod_pre = None
            if pnum:
                prod_pre = catalog_indexes["by_group_pnum"].get((exp_raw.lower(), pnum.lower()))
            if not prod_pre:
                prod_pre = catalog_indexes["by_group_name"].get((exp_raw.lower(), name_raw.lower()))
            if not prod_pre:
                prod_pre = catalog_indexes["by_group_clean_name"].get((exp_raw.lower(), name_raw.lower()))
            if not prod_pre:
                prod_pre = catalog_indexes["by_name"].get(name_raw.lower())
            if not prod_pre and row_pid:
                prod_pre = catalog_indexes["by_pid"].get(int(row_pid)) or catalog_indexes["by_pid"].get(str(row_pid))

            prod_id_pre = prod_pre["productId"] if prod_pre else (int(row_pid) if row_pid else None)
            name_pre = sanitize_card_name(prod_pre.get("name") or name_raw) if prod_pre else sanitize_card_name(name_raw)
            exp_pre = prod_pre.get("groupName") or exp_raw if prod_pre else exp_raw

            effective_acq_date = lookup_first_seen_date(
                cur, item_type, prod_id_pre, name_pre, exp_pre, pnum, finish_raw
            ) or date_str

        prod, ctx, distinct_key, is_matched, label = match_collection_item(
            row, catalog_indexes, effective_acq_date
        )

        if is_matched:
            if distinct_key not in seen_distinct:
                matched_distinct.add(distinct_key)
        else:
            if distinct_key not in seen_distinct:
                unmatched_item_labels[distinct_key] = label
        seen_distinct.add(distinct_key)

        market_price, unit_low, unit_mid, unit_high, line_total = resolve_item_market_price(
            prod, ctx, date_str, cur
        )

        if market_price is not None and market_price > 0.0:
            total_value += line_total
        else:
            if distinct_key not in zero_price_item_labels:
                zero_price_item_labels[distinct_key] = label

        if ctx.item_type == "Sealed":
            total_sealed += ctx.qty
        else:
            total_cards += ctx.qty

        baseline_price = resolve_and_persist_baseline(
            cur, ctx, date_str, market_price, cache_dir, session_price_cache
        )

        if market_price is not None and baseline_price is not None and market_price > 0.0 and baseline_price > 0.0:
            card_gain_dollar = round((market_price - baseline_price) * ctx.qty, 2)
            card_gain_pct = round(((market_price - baseline_price) / baseline_price) * 100.0, 2)
            total_lifetime_gain += card_gain_dollar
        else:
            card_gain_dollar = None
            card_gain_pct = None

        cur.execute("""
        INSERT OR REPLACE INTO daily_snapshots (
            date, card_key, quantity, unit_market_price, unit_low_price, unit_mid_price, unit_high_price,
            line_total, baseline_price, lifetime_gain_dollar, lifetime_gain_pct
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str, ctx.card_key, ctx.qty, market_price, unit_low, unit_mid, unit_high,
            line_total, baseline_price, card_gain_dollar, card_gain_pct
        ))

    total_value = round(total_value, 2)
    total_lifetime_gain = round(total_lifetime_gain, 2)
    portfolio_baseline = total_value - total_lifetime_gain
    lifetime_pct_gain = round((total_lifetime_gain / portfolio_baseline) * 100.0, 2) if portfolio_baseline > 0 else 0.0

    l7d_dollar_delta, l7d_pct_delta = calculate_l7d_metrics(cur, date_str)

    if not collection_updated_at and collection_path and os.path.isfile(collection_path):
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(collection_path)).astimezone()
        collection_updated_at = mtime.strftime("%Y-%m-%d %I:%M %p")

    if not collection_source:
        if collection_path:
            collection_source = f"CSV ({Path(collection_path).name})"
        else:
            collection_source = "CSV"

    if total_cost_basis is None:
        total_cost_basis = 0.0
    else:
        total_cost_basis = float(total_cost_basis)

    total_cost_basis = round(total_cost_basis, 2)
    if total_cost_basis > 0:
        net_unrealized_gain = round(total_value - total_cost_basis, 2)
        net_unrealized_pct = round((net_unrealized_gain / total_cost_basis) * 100.0, 2)
    else:
        net_unrealized_gain = 0.0
        net_unrealized_pct = 0.0

    persist_portfolio_summary(
        cur, conn, date_str, total_value, total_cards, unique_items,
        l7d_dollar_delta, l7d_pct_delta, total_lifetime_gain, lifetime_pct_gain,
        total_sealed, collection_updated_at, collection_source,
        total_cost_basis, net_unrealized_gain, net_unrealized_pct,
        purchases_updated_at,
    )

    conn.close()

    total_distinct = len(seen_distinct)
    n_matched = len(matched_distinct)
    match_pct = (n_matched / total_distinct * 100) if total_distinct > 0 else 0.0
    print(f"Matched {n_matched}/{total_distinct} distinct collection items to catalog products ({match_pct:.1f}%).")

    unmatched_labels = list(unmatched_item_labels.values())
    if unmatched_labels:
        print(f"Warning: {len(unmatched_labels)} collection item(s) had no catalog match and will use carry-forward or fallback prices:")
        for item in unmatched_labels[:10]:
            print(f"  - {item}")
        if len(unmatched_labels) > 10:
            print(f"  ... and {len(unmatched_labels) - 10} more. Check expansion names and print numbers against cards.json.")

    zero_price_labels = list(zero_price_item_labels.values())
    if zero_price_labels:
        print(f"Notice: {len(zero_price_labels)} item(s) had no market price and were excluded from the portfolio total:")
        for item in zero_price_labels[:10]:
            print(f"  - {item}")
        if len(zero_price_labels) > 10:
            print(f"  ... and {len(zero_price_labels) - 10} more.")

    cost_info = f" | Cost Basis: ${total_cost_basis:,.2f} | Net Unrealized Gain: {'+' if net_unrealized_gain >= 0 else ''}${net_unrealized_gain:,.2f} ({'+' if net_unrealized_pct >= 0 else ''}{net_unrealized_pct:.2f}%)" if total_cost_basis > 0 else ""
    print(f"Valuation complete: Total Value: ${total_value:,.2f}{cost_info} | Cards: {total_cards} | Sealed: {total_sealed} | Lifetime Gain: {'+' if total_lifetime_gain >= 0 else ''}${total_lifetime_gain:,.2f} ({'+' if lifetime_pct_gain >= 0 else ''}{lifetime_pct_gain:.2f}%)")
    return {
        "total_value": total_value,
        "total_cards": total_cards,
        "total_sealed": total_sealed,
        "unique_items": unique_items,
        "lifetime_dollar_gain": total_lifetime_gain,
        "lifetime_pct_gain": lifetime_pct_gain,
        "total_cost_basis": total_cost_basis,
        "net_unrealized_gain": net_unrealized_gain,
        "net_unrealized_pct": net_unrealized_pct,
        "purchases_updated_at": purchases_updated_at,
    }
