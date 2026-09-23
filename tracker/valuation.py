"""
Valuation engine for Cyberpunk TCG collections.
Matches inventory records against TCGplayer market prices, computes rolling metrics,
and persists historical daily snapshots into a local SQLite database.
"""

import datetime
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Dict, Any, List, Optional

from tracker.validation import (
    validate_collection_file,
    validate_sealed_file,
    format_validation_report,
    is_sealed_product,
    extract_date_from_text,
    CollectionValidationError,
)


def sanitize_card_name(name: str) -> str:
    """Strips upstream disambiguation suffixes (e.g. (Epic), (Secret), (IO), (a)) from catalog card names."""
    if not name:
        return ""
    return re.sub(r"\s*\((?:Epic|Rare|Secret|Iconic|ILegend|IO|ISecret|007|[ab])\)$", "", name.strip(), flags=re.IGNORECASE)


def get_earliest_price_date(cache_dir: str, cur: Optional[sqlite3.Cursor] = None) -> Optional[str]:
    """Finds the earliest available price date across price cache files and database snapshots."""
    dates = []
    candidates = [cache_dir]
    repo_prices = str(Path(__file__).resolve().parent.parent / "prices")
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
        except Exception:
            pass
    return min(dates) if dates else None


_HISTORICAL_PRICE_CACHE = {}


def get_historical_market_price(
    cache_dir: str,
    target_date: str,
    prod_id: Optional[int],
    expansion: str,
    print_number: Optional[str],
    name: str,
    finish: str,
) -> Optional[float]:
    """Retrieves market price for a product on target_date from price cache files."""
    global _HISTORICAL_PRICE_CACHE
    cache_key = (os.path.abspath(cache_dir), target_date)
    if cache_key not in _HISTORICAL_PRICE_CACHE:
        candidates = [
            os.path.join(cache_dir, f"{target_date}.json"),
            str(Path(__file__).resolve().parent.parent / "prices" / f"{target_date}.json"),
        ]
        target_file = next((c for c in candidates if os.path.isfile(c)), None)
        if not target_file:
            _HISTORICAL_PRICE_CACHE[cache_key] = {}
        else:
            try:
                with open(target_file, "r", encoding="utf-8") as f:
                    _HISTORICAL_PRICE_CACHE[cache_key] = json.load(f)
            except Exception:
                _HISTORICAL_PRICE_CACHE[cache_key] = {}

    cache_data = _HISTORICAL_PRICE_CACHE.get(cache_key, {})
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


def clear_historical_price_cache() -> None:
    """Clears in-memory cache of historical prices."""
    global _HISTORICAL_PRICE_CACHE
    _HISTORICAL_PRICE_CACHE.clear()


def init_database(db_path: str):
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
    purchase_history_dir: Optional[str] = None,
    purchase_ledger_path: Optional[str] = None,
    purchase_cache_path: Optional[str] = None,
    total_cost_basis: Optional[float] = None,
    reparse_purchases: bool = False,
    purchases_updated_at: Optional[str] = None,
) -> Dict[str, Any]:
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

    cache_file = price_file if (price_file and os.path.isfile(price_file)) else os.path.join(cache_dir, f"{date_str}.json")
    if not os.path.exists(cache_file):
        raise FileNotFoundError(f"Price cache file not found for {date_str}: {cache_file}")

    with open(cache_file, "r", encoding="utf-8") as f:
        cache_data = json.load(f)

    if "products" in cache_data:
        prods = cache_data.get("products", {})
    elif "prices" in cache_data:
        cards_candidates = [
            os.path.join(cache_dir, "cards.json"),
            os.path.join(os.path.dirname(cache_dir), "cards.json"),
            str(Path(__file__).resolve().parent.parent / "cards.json"),
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

    earliest_price_date = get_earliest_price_date(cache_dir, cur)

    card_items = list(collection_rows or [])
    sealed_items = list(sealed_rows or [])

    # Deduplicate if sealed items are already present in collection_rows
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

    # Expansion cross-reference: warn on names that don't match any catalog group.
    # Only run when catalog_group_names is non-empty; an empty set means cards.json
    # lacks groupName metadata, which would false-alarm on every expansion.
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
    # Use sets of distinct item keys to avoid counting multi-lot tranches as separate entries.
    unmatched_item_labels: dict = {}   # key -> label (ordered insertion)
    zero_price_item_labels: dict = {}  # key -> label (ordered insertion)
    matched_distinct: set = set()
    seen_distinct: set = set()

    for row in all_items:
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
            prod = catalog_by_group_pnum.get((expansion.lower(), print_number.lower()))
        if not prod:
            prod = catalog_by_group_name.get((expansion.lower(), name.lower()))
        if not prod:
            prod = catalog_by_group_clean_name.get((expansion.lower(), name.lower()))
        if not prod:
            prod = catalog_by_name.get(name.lower())
        if not prod and row_pid:
            prod = catalog_by_pid.get(int(row_pid)) or catalog_by_pid.get(str(row_pid))

        prod_id = prod["productId"] if prod else (int(row_pid) if row_pid else None)
        # Build a key that identifies this item independent of lot tranche, so multi-lot
        # expansions don't count as multiple distinct entries in diagnostics.
        distinct_key = (item_type, expansion.lower(), (print_number or name).lower(), finish.lower())
        if prod:
            if distinct_key not in seen_distinct:
                matched_distinct.add(distinct_key)
            name = sanitize_card_name(prod.get("name") or name)
            expansion = prod.get("groupName") or expansion
            rarity = prod.get("rarity") or row.get("rarity")
            color = (prod.get("color") or row.get("color") or "").strip()
            card_type = prod.get("cardType") or row.get("card_type")
        else:
            label = f"'{name}' ({print_number or 'N/A'}, {expansion})"
            if distinct_key not in seen_distinct:
                unmatched_item_labels[distinct_key] = label
            name = sanitize_card_name(name)
            rarity = row.get("rarity")
            color = (row.get("color") or "").strip()
            card_type = row.get("card_type")
        seen_distinct.add(distinct_key)

        acq_date_raw = (row.get("acquisitionDate") or "").strip()
        if not acq_date_raw and row.get("notes"):
            acq_date_raw = extract_date_from_text(row.get("notes")) or ""

        if acq_date_raw:
            if earliest_price_date and acq_date_raw < earliest_price_date:
                effective_acq_date = earliest_price_date
            else:
                effective_acq_date = acq_date_raw
        else:
            effective_acq_date = date_str

        if item_type == "Sealed":
            if not acq_date_raw:
                cur.execute(
                    "SELECT card_key, first_seen_date FROM card_metadata WHERE item_type = 'Sealed' AND (product_id = ? OR name = ?) ORDER BY first_seen_date ASC LIMIT 1",
                    (prod_id, name),
                )
                existing_meta = cur.fetchone()
                if existing_meta and existing_meta[1]:
                    effective_acq_date = existing_meta[1]
                else:
                    effective_acq_date = date_str
            card_key = f"SEALED::{expansion}::{prod_id or name}::{effective_acq_date}"
            rarity = "Sealed"
        else:
            if not acq_date_raw:
                cur.execute(
                    "SELECT card_key, first_seen_date FROM card_metadata WHERE item_type = 'Card' AND expansion = ? AND print_number = ? AND finish = ? ORDER BY first_seen_date ASC LIMIT 1",
                    (expansion, print_number, finish),
                )
                existing_meta = cur.fetchone()
                if existing_meta and existing_meta[1]:
                    effective_acq_date = existing_meta[1]
                else:
                    effective_acq_date = date_str
            card_key = f"{expansion}::{print_number}::{finish}::{effective_acq_date}"

        sub_type = "Foil" if finish.lower() == "foil" else "Normal"
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
            """, (date_str, prod_id, expansion, print_number, finish, expansion, name))
            prev_price_row = cur.fetchone()
            if prev_price_row and prev_price_row[0] is not None and prev_price_row[0] > 0.0:
                market_price = prev_price_row[0]
            elif fallback_price > 0.0:
                market_price = fallback_price
            else:
                market_price = None

        if market_price is not None and market_price > 0.0:
            unit_low = prices.get("lowPrice") or market_price
            unit_mid = prices.get("midPrice") or market_price
            unit_high = prices.get("highPrice") or market_price
            line_total = round(qty * market_price, 2)
            total_value += line_total
        else:
            if distinct_key not in zero_price_item_labels:
                zero_price_item_labels[distinct_key] = f"'{name}' ({print_number or 'N/A'}, {expansion})"
            market_price = None
            unit_low = None
            unit_mid = None
            unit_high = None
            line_total = None

        if item_type == "Sealed":
            total_sealed += qty
        else:
            total_cards += qty

        cur.execute("SELECT baseline_market_price, color, item_type, name, rarity, card_type FROM card_metadata WHERE card_key = ?", (card_key,))
        meta_res = cur.fetchone()
        if meta_res:
            baseline_price = meta_res[0]
            existing_color = meta_res[1]
            existing_item_type = meta_res[2] if len(meta_res) > 2 else "Card"
            existing_name = meta_res[3] if len(meta_res) > 3 else None
            existing_rarity = meta_res[4] if len(meta_res) > 4 else None
            existing_card_type = meta_res[5] if len(meta_res) > 5 else None
            if color and existing_color != color:
                cur.execute("UPDATE card_metadata SET color = ? WHERE card_key = ?", (color, card_key))
            if name and existing_name != name:
                cur.execute("UPDATE card_metadata SET name = ? WHERE card_key = ?", (name, card_key))
            if rarity and existing_rarity != rarity:
                cur.execute("UPDATE card_metadata SET rarity = ? WHERE card_key = ?", (rarity, card_key))
            if not existing_item_type or existing_item_type != item_type:
                cur.execute("UPDATE card_metadata SET item_type = ? WHERE card_key = ?", (item_type, card_key))
            if card_type and existing_card_type != card_type:
                cur.execute("UPDATE card_metadata SET card_type = ? WHERE card_key = ?", (card_type, card_key))
            if baseline_price is None or baseline_price <= 0.0:
                if fallback_price > 0.0:
                    baseline_price = fallback_price
                elif market_price is not None and market_price > 0.0:
                    baseline_price = market_price
                if baseline_price and baseline_price > 0.0:
                    cur.execute("UPDATE card_metadata SET baseline_market_price = ? WHERE card_key = ?", (baseline_price, card_key))
        else:
            if item_type == "Sealed" and fallback_price > 0.0:
                baseline_price = fallback_price
            else:
                baseline_price = None
                if effective_acq_date and effective_acq_date < date_str:
                    hist_price = get_historical_market_price(
                        cache_dir=cache_dir,
                        target_date=effective_acq_date,
                        prod_id=prod_id,
                        expansion=expansion,
                        print_number=print_number,
                        name=name,
                        finish=finish,
                    )
                    if hist_price is not None and hist_price > 0.0:
                        baseline_price = hist_price

                if baseline_price is None or baseline_price <= 0.0:
                    if market_price is not None and market_price > 0.0:
                        baseline_price = market_price
                    elif fallback_price > 0.0:
                        baseline_price = fallback_price
                    else:
                        baseline_price = None

            first_seen = effective_acq_date
            cur.execute("""
            INSERT INTO card_metadata (card_key, product_id, name, print_number, expansion, finish, rarity, color, first_seen_date, baseline_market_price, item_type, card_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (card_key, prod_id, name, print_number, expansion, finish, rarity, color, first_seen, baseline_price, item_type, card_type))

        if market_price is not None and baseline_price is not None and market_price > 0.0 and baseline_price > 0.0:
            card_gain_dollar = round((market_price - baseline_price) * qty, 2)
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
            date_str, card_key, qty, market_price, unit_low, unit_mid, unit_high,
            line_total, baseline_price, card_gain_dollar, card_gain_pct
        ))

    total_value = round(total_value, 2)
    total_lifetime_gain = round(total_lifetime_gain, 2)
    portfolio_baseline = total_value - total_lifetime_gain
    lifetime_pct_gain = round((total_lifetime_gain / portfolio_baseline) * 100.0, 2) if portfolio_baseline > 0 else 0.0

    cur.execute("""
    SELECT date
    FROM portfolio_daily_summary
    WHERE date < ?
    ORDER BY date DESC
    LIMIT 7
    """, (date_str,))
    history_dates = cur.fetchall()

    if history_dates:
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
    else:
        l7d_dollar_delta = 0.0
        l7d_pct_delta = 0.0

    if not collection_updated_at and collection_path and os.path.isfile(collection_path):
        mtime = datetime.datetime.fromtimestamp(os.path.getmtime(collection_path)).astimezone()
        collection_updated_at = mtime.strftime("%Y-%m-%d %I:%M %p")

    if not collection_source:
        if collection_path:
            collection_source = f"CSV ({Path(collection_path).name})"
        else:
            collection_source = "CSV"

    p_dir = purchase_history_dir
    if not p_dir and collection_path:
        cand = Path(collection_path).parent / "purchase_history"
        if cand.is_dir():
            p_dir = str(cand.resolve())

    if total_cost_basis is None:
        if p_dir:
            from tracker.purchases import sync_purchase_history
            total_cost_basis, _ = sync_purchase_history(
                purchase_dir=p_dir,
                cache_path=purchase_cache_path,
                ledger_path=purchase_ledger_path,
                reparse=reparse_purchases,
            )
        elif purchase_ledger_path and os.path.isfile(purchase_ledger_path):
            from tracker.purchases import sync_purchase_history
            total_cost_basis, _ = sync_purchase_history(
                purchase_dir=None,
                cache_path=purchase_cache_path,
                ledger_path=purchase_ledger_path,
                reparse=reparse_purchases,
            )
        else:
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

    if not purchases_updated_at and total_cost_basis > 0:
        cache_file = purchase_cache_path
        if not cache_file and p_dir:
            cache_file = os.path.join(p_dir, "purchase_history_cache.json")
        if cache_file and os.path.isfile(cache_file):
            try:
                from tracker.purchases import load_purchase_cache
                c_data = load_purchase_cache(cache_file)
                raw_updated = c_data.get("last_updated")
                if raw_updated:
                    try:
                        dt = datetime.datetime.fromisoformat(raw_updated)
                        purchases_updated_at = dt.strftime("%Y-%m-%d %I:%M %p")
                    except ValueError:
                        try:
                            dt = datetime.datetime.strptime(raw_updated, "%Y-%m-%d %H:%M:%S")
                            purchases_updated_at = dt.strftime("%Y-%m-%d %I:%M %p")
                        except ValueError:
                            purchases_updated_at = raw_updated
                else:
                    mtime = datetime.datetime.fromtimestamp(os.path.getmtime(cache_file)).astimezone()
                    purchases_updated_at = mtime.strftime("%Y-%m-%d %I:%M %p")
            except Exception:
                pass
        elif purchase_ledger_path and os.path.isfile(purchase_ledger_path):
            mtime = datetime.datetime.fromtimestamp(os.path.getmtime(purchase_ledger_path)).astimezone()
            purchases_updated_at = mtime.strftime("%Y-%m-%d %I:%M %p")

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
    conn.close()

    # Post-loop diagnostics — all counts and lists are over distinct items, not expanded lot tranches.
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
