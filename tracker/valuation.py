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

from tracker.validation import validate_collection_file, CollectionValidationError


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
        lifetime_pct_gain REAL
    )
    """)

    conn.commit()
    return conn


def calculate_portfolio_valuation(
    date_str: str,
    collection_path: str,
    cache_dir: str,
    db_path: str,
    force: bool = False,
    collection_rows: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    if collection_rows is None:
        is_valid, validation_errors, collection_rows = validate_collection_file(collection_path)
        if not is_valid:
            error_msg = f"Collection validation failed for '{collection_path}':\n" + "\n".join(f" - {err}" for err in validation_errors)
            raise CollectionValidationError(error_msg, errors=validation_errors)

    conn = init_database(db_path)
    cur = conn.cursor()

    if not force:
        cur.execute("SELECT COUNT(*) FROM portfolio_daily_summary WHERE date = ?", (date_str,))
        if cur.fetchone()[0] > 0:
            print(f"Snapshot for {date_str} already exists in database. Use force=True to overwrite.")
            cur.execute("""
            SELECT total_value, total_cards, unique_items, lifetime_dollar_gain, lifetime_pct_gain
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
            }

    cache_file = os.path.join(cache_dir, f"{date_str}.json")
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
    else:
        prods = {}

    catalog_by_group_pnum = {}
    for p in prods.values():
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

    print(f"Calculating portfolio valuation for {date_str} across {len(collection_rows)} collection rows...")

    total_value = 0.0
    total_cards = 0
    unique_items = len(collection_rows)
    total_lifetime_gain = 0.0

    for row in collection_rows:
        name = row["name"].strip()
        expansion = row["expansion"].strip()
        print_number = row["printNumber"].strip()
        finish = row["finish"].strip()
        color = row.get("color", "").strip()
        qty = int(row.get("totalQtyOwned", 1))
        fallback_price = float(row.get("price") or 0.0)

        card_key = f"{expansion}::{print_number}::{finish}"

        prod = catalog_by_group_pnum.get((expansion.lower(), print_number.lower()))
        prod_id = prod["productId"] if prod else None
        rarity = prod.get("rarity") if prod else row.get("rarity")

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
            market_price = fallback_price

        unit_low = prices.get("lowPrice") or market_price
        unit_mid = prices.get("midPrice") or market_price
        unit_high = prices.get("highPrice") or market_price

        line_total = round(qty * market_price, 2)
        total_value += line_total
        total_cards += qty

        cur.execute("SELECT baseline_market_price, color FROM card_metadata WHERE card_key = ?", (card_key,))
        meta_res = cur.fetchone()
        if meta_res:
            baseline_price = meta_res[0]
            existing_color = meta_res[1]
            if (not existing_color or existing_color == "") and color:
                cur.execute("UPDATE card_metadata SET color = ? WHERE card_key = ?", (color, card_key))
        else:
            baseline_price = market_price
            cur.execute("""
            INSERT INTO card_metadata (card_key, product_id, name, print_number, expansion, finish, rarity, color, first_seen_date, baseline_market_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (card_key, prod_id, name, print_number, expansion, finish, rarity, color, date_str, baseline_price))

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
        l7d_dollar_delta, l7d_pct_delta, lifetime_dollar_gain, lifetime_pct_gain
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        date_str, total_value, total_cards, unique_items,
        l7d_dollar_delta, l7d_pct_delta, total_lifetime_gain, lifetime_pct_gain
    ))

    conn.commit()
    conn.close()

    print(f"Valuation complete: Total Value: ${total_value:,.2f} | Cards: {total_cards} | Lifetime Gain: {'+' if total_lifetime_gain >= 0 else ''}${total_lifetime_gain:,.2f} ({'+' if lifetime_pct_gain >= 0 else ''}{lifetime_pct_gain:.2f}%)")
    return {
        "total_value": total_value,
        "total_cards": total_cards,
        "unique_items": unique_items,
        "lifetime_dollar_gain": total_lifetime_gain,
        "lifetime_pct_gain": lifetime_pct_gain,
    }
