"""
Valuation engine for Cyberpunk TCG collections.
Matches inventory records against TCGplayer market prices, computes rolling metrics,
and persists historical daily snapshots into a local SQLite database.
"""

import datetime
import json
import os
from pathlib import Path
import sqlite3
from typing import Dict, Any, List, Optional

from tracker.validation import validate_collection_file, validate_sealed_file, CollectionValidationError


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

    # Migrate legacy 3-part sealed keys (SEALED::{expansion}::{productId}) to lot keys with acquisitionDate
    cur.execute("SELECT card_key, first_seen_date FROM card_metadata WHERE item_type = 'Sealed'")
    for old_key, first_seen in cur.fetchall():
        parts = old_key.split("::")
        if len(parts) == 3:
            migrated_date = first_seen or "2026-09-11"
            new_key = f"{old_key}::{migrated_date}"
            cur.execute("UPDATE card_metadata SET card_key = ? WHERE card_key = ?", (new_key, old_key))
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
        total_sealed INTEGER DEFAULT 0
    )
    """)

    cur.execute("PRAGMA table_info(portfolio_daily_summary)")
    sum_cols = [col[1] for col in cur.fetchall()]
    if "total_sealed" not in sum_cols:
        cur.execute("ALTER TABLE portfolio_daily_summary ADD COLUMN total_sealed INTEGER DEFAULT 0")

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
) -> Dict[str, Any]:
    if collection_rows is None:
        is_valid, validation_errors, collection_rows = validate_collection_file(collection_path)
        if not is_valid:
            error_msg = f"Collection validation failed for '{collection_path}':\n" + "\n".join(f" - {err}" for err in validation_errors)
            raise CollectionValidationError(error_msg, errors=validation_errors)

    if sealed_rows is None and sealed_path and os.path.exists(sealed_path):
        is_valid_sealed, sealed_errors, sealed_rows = validate_sealed_file(sealed_path)
        if not is_valid_sealed:
            error_msg = f"Sealed inventory validation failed for '{sealed_path}':\n" + "\n".join(f" - {err}" for err in sealed_errors)
            raise CollectionValidationError(error_msg, errors=sealed_errors)

    conn = init_database(db_path)
    cur = conn.cursor()

    if not force:
        cur.execute("SELECT COUNT(*) FROM portfolio_daily_summary WHERE date = ?", (date_str,))
        if cur.fetchone()[0] > 0:
            print(f"Snapshot for {date_str} already exists in database. Use force=True to overwrite.")
            cur.execute("""
            SELECT total_value, total_cards, unique_items, lifetime_dollar_gain, lifetime_pct_gain,
                   COALESCE(total_sealed, 0)
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
    for p in prods.values():
        pid = p.get("productId")
        if pid:
            catalog_by_pid[int(pid)] = p
            catalog_by_pid[str(pid)] = p
        g = p.get("groupName", "").strip().lower()
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

    card_items = list(collection_rows or [])
    sealed_items = list(sealed_rows or [])
    all_items = card_items + sealed_items

    print(f"Calculating portfolio valuation for {date_str} across {len(card_items)} card rows and {len(sealed_items)} sealed items...")

    total_value = 0.0
    total_cards = 0
    total_sealed = 0
    unique_items = len(all_items)
    total_lifetime_gain = 0.0

    for row in all_items:
        item_type = row.get("item_type") or "Card"
        name = row["name"].strip()
        expansion = row["expansion"].strip()
        print_number = (row.get("printNumber") or "").strip() or None
        finish = (row.get("finish") or "Standard").strip()
        color = (row.get("color") or "").strip()
        qty = int(row.get("totalQtyOwned", 1))
        fallback_price = float(row.get("price") or 0.0)
        row_pid = row.get("productId")

        prod = None
        if row_pid:
            prod = catalog_by_pid.get(int(row_pid)) or catalog_by_pid.get(str(row_pid))
        if not prod and print_number:
            prod = catalog_by_group_pnum.get((expansion.lower(), print_number.lower()))

        prod_id = prod["productId"] if prod else (int(row_pid) if row_pid else None)
        rarity = prod.get("rarity") if prod else row.get("rarity")

        if item_type == "Sealed":
            acq_date = (row.get("acquisitionDate") or "").strip()
            card_key = f"SEALED::{expansion}::{prod_id or name}::{acq_date}" if acq_date else f"SEALED::{expansion}::{prod_id or name}"
            rarity = "Sealed"
        else:
            card_key = f"{expansion}::{print_number}::{finish}"

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
            SELECT unit_market_price
            FROM daily_snapshots
            WHERE card_key = ? AND date < ? AND unit_market_price > 0.0
            ORDER BY date DESC
            LIMIT 1
            """, (card_key, date_str))
            prev_price_row = cur.fetchone()
            if prev_price_row and prev_price_row[0] > 0.0:
                market_price = prev_price_row[0]
            elif fallback_price > 0.0:
                market_price = fallback_price
            else:
                cur.execute("SELECT baseline_market_price FROM card_metadata WHERE card_key = ?", (card_key,))
                base_row = cur.fetchone()
                if base_row and base_row[0] and base_row[0] > 0.0:
                    market_price = base_row[0]
                else:
                    market_price = fallback_price

        unit_low = prices.get("lowPrice") or market_price
        unit_mid = prices.get("midPrice") or market_price
        unit_high = prices.get("highPrice") or market_price

        line_total = round(qty * market_price, 2)
        total_value += line_total
        if item_type == "Sealed":
            total_sealed += qty
        else:
            total_cards += qty

        cur.execute("SELECT baseline_market_price, color, item_type FROM card_metadata WHERE card_key = ?", (card_key,))
        meta_res = cur.fetchone()
        if meta_res:
            baseline_price = meta_res[0]
            existing_color = meta_res[1]
            existing_item_type = meta_res[2] if len(meta_res) > 2 else "Card"
            if (not existing_color or existing_color == "") and color:
                cur.execute("UPDATE card_metadata SET color = ? WHERE card_key = ?", (color, card_key))
            if not existing_item_type or existing_item_type != item_type:
                cur.execute("UPDATE card_metadata SET item_type = ? WHERE card_key = ?", (item_type, card_key))
            if item_type == "Sealed" and fallback_price > 0.0 and baseline_price != fallback_price:
                baseline_price = fallback_price
                cur.execute("UPDATE card_metadata SET baseline_market_price = ? WHERE card_key = ?", (baseline_price, card_key))
            elif (baseline_price is None or baseline_price <= 0.0) and market_price > 0.0:
                baseline_price = market_price
                cur.execute("UPDATE card_metadata SET baseline_market_price = ? WHERE card_key = ?", (baseline_price, card_key))
        else:
            if item_type == "Sealed" and fallback_price > 0.0:
                baseline_price = fallback_price
            else:
                baseline_price = market_price
            first_seen = acq_date if (item_type == "Sealed" and acq_date) else date_str
            cur.execute("""
            INSERT INTO card_metadata (card_key, product_id, name, print_number, expansion, finish, rarity, color, first_seen_date, baseline_market_price, item_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (card_key, prod_id, name, print_number, expansion, finish, rarity, color, first_seen, baseline_price, item_type))

        card_gain_dollar = round((market_price - baseline_price) * qty, 2)
        card_gain_pct = round(((market_price - baseline_price) / baseline_price) * 100.0, 2) if baseline_price > 0 else 0.0
        total_lifetime_gain += card_gain_dollar

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
    SELECT date, total_value
    FROM portfolio_daily_summary
    WHERE date < ?
    ORDER BY date DESC
    LIMIT 7
    """, (date_str,))
    history_rows = cur.fetchall()

    if history_rows:
        baseline_l7d = history_rows[-1][1]
        l7d_dollar_delta = round(total_value - baseline_l7d, 2)
        l7d_pct_delta = round((l7d_dollar_delta / baseline_l7d) * 100.0, 2) if baseline_l7d > 0 else 0.0
    else:
        l7d_dollar_delta = 0.0
        l7d_pct_delta = 0.0

    cur.execute("""
    INSERT OR REPLACE INTO portfolio_daily_summary (
        date, total_value, total_cards, unique_items,
        l7d_dollar_delta, l7d_pct_delta, lifetime_dollar_gain, lifetime_pct_gain,
        total_sealed
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        date_str, total_value, total_cards, unique_items,
        l7d_dollar_delta, l7d_pct_delta, total_lifetime_gain, lifetime_pct_gain,
        total_sealed
    ))

    conn.commit()
    conn.close()

    print(f"Valuation complete: Total Value: ${total_value:,.2f} | Cards: {total_cards} | Sealed: {total_sealed} | Lifetime Gain: {'+' if total_lifetime_gain >= 0 else ''}${total_lifetime_gain:,.2f} ({'+' if lifetime_pct_gain >= 0 else ''}{lifetime_pct_gain:.2f}%)")
    return {
        "total_value": total_value,
        "total_cards": total_cards,
        "total_sealed": total_sealed,
        "unique_items": unique_items,
        "lifetime_dollar_gain": total_lifetime_gain,
        "lifetime_pct_gain": lifetime_pct_gain,
    }
