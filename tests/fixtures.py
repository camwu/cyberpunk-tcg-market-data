"""
Reusable test fixture factories and database seeding helpers for unit and integration test suites.
All path helpers accept explicit directories to ensure complete decoupling from repository paths.
"""

import csv
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple


def seed_metadata(conn: sqlite3.Connection, items: List[Dict[str, Any]]) -> None:
    """Seeds records into the card_metadata table."""
    cur = conn.cursor()
    for it in items:
        cur.execute("""
        INSERT OR REPLACE INTO card_metadata (
            card_key, product_id, name, print_number, expansion, finish, rarity, color,
            first_seen_date, baseline_market_price, item_type, card_type
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            it["card_key"],
            it.get("product_id"),
            it.get("name"),
            it.get("print_number"),
            it.get("expansion"),
            it.get("finish", "Standard"),
            it.get("rarity", "Common"),
            it.get("color", ""),
            it.get("first_seen_date"),
            it.get("baseline_market_price"),
            it.get("item_type", "Card"),
            it.get("card_type"),
        ))
    conn.commit()


def seed_snapshots(conn: sqlite3.Connection, date_str: str, snapshots: List[Dict[str, Any]]) -> None:
    """Seeds records into the daily_snapshots table for a given date."""
    cur = conn.cursor()
    for s in snapshots:
        unit_price = s.get("unit_market_price")
        qty = s.get("quantity", 1)
        line_total = s.get("line_total", round(qty * unit_price, 2) if unit_price is not None else 0.0)
        baseline = s.get("baseline_price")
        gain_dollar = s.get("lifetime_gain_dollar")
        gain_pct = s.get("lifetime_gain_pct")
        if gain_dollar is None and unit_price is not None and baseline is not None and baseline > 0:
            gain_dollar = round((unit_price - baseline) * qty, 2)
            gain_pct = round(((unit_price - baseline) / baseline) * 100.0, 2)

        cur.execute("""
        INSERT OR REPLACE INTO daily_snapshots (
            date, card_key, quantity, unit_market_price, unit_low_price, unit_mid_price, unit_high_price,
            line_total, baseline_price, lifetime_gain_dollar, lifetime_gain_pct
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            date_str,
            s["card_key"],
            qty,
            unit_price,
            s.get("unit_low_price", unit_price),
            s.get("unit_mid_price", unit_price),
            s.get("unit_high_price", unit_price),
            line_total,
            baseline,
            gain_dollar,
            gain_pct,
        ))
    conn.commit()


def seed_summary(
    conn: sqlite3.Connection,
    date_str: str,
    total_value: float,
    total_cards: int,
    cost_basis: float = 0.0,
    **kwargs,
) -> None:
    """Seeds a row into the portfolio_daily_summary table."""
    cur = conn.cursor()
    total_sealed = kwargs.get("total_sealed", 0)
    unique_items = kwargs.get("unique_items", total_cards + total_sealed)
    lifetime_gain = kwargs.get("lifetime_gain", 0.0)
    lifetime_pct = kwargs.get("lifetime_pct", 0.0)
    l7d_dollar_delta = kwargs.get("l7d_dollar_delta", kwargs.get("l7d_gain", 0.0))
    l7d_pct_delta = kwargs.get("l7d_pct_delta", kwargs.get("l7d_pct", 0.0))
    collection_source = kwargs.get("collection_source", "CSV (test.csv)")
    collection_updated = kwargs.get("collection_updated_at", date_str)
    purchases_updated = kwargs.get("purchases_updated_at")
    unrealized_gain = kwargs.get("net_unrealized_gain", round(total_value - cost_basis, 2) if cost_basis > 0 else 0.0)
    unrealized_pct = kwargs.get("net_unrealized_pct", round((unrealized_gain / cost_basis) * 100.0, 2) if cost_basis > 0 else 0.0)

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
        l7d_dollar_delta, l7d_pct_delta, lifetime_gain, lifetime_pct,
        total_sealed, collection_updated, collection_source,
        cost_basis, unrealized_gain, unrealized_pct,
        purchases_updated,
    ))
    conn.commit()


def seed_purchases(conn: sqlite3.Connection, purchases: List[Tuple[str, str, float, str, str]]) -> None:
    """Seeds records into the purchase_history table."""
    cur = conn.cursor()
    for p in purchases:
        cur.execute("""
        INSERT INTO purchase_history (date, merchant, amount, description, filename)
        VALUES (?, ?, ?, ?, ?)
        """, p)
    conn.commit()


def create_test_catalog(cache_dir: Path, cards: Optional[Dict[str, Any]] = None) -> Path:
    """Creates a cards.json catalog file in cache_dir with default test products."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = cache_dir / "cards.json"
    if cards is None:
        cards = {
            "101": {
                "productId": 101,
                "name": "Johnny Silverhand",
                "cleanName": "Johnny Silverhand",
                "groupId": 1000,
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "001",
                "rarity": "Epic",
                "color": "Yellow",
                "cardType": "Character",
            },
            "102": {
                "productId": 102,
                "name": "Welcome to Night City - Beta Booster Box",
                "cleanName": "Welcome to Night City Beta Booster Box",
                "groupId": 1000,
                "groupName": "Welcome to Night City - Beta",
                "printNumber": "",
                "rarity": "Sealed",
                "color": "",
                "cardType": "Sealed",
            },
        }
    with open(catalog_path, "w", encoding="utf-8") as f:
        json.dump(cards, f, indent=2)
    return catalog_path


def create_daily_prices(cache_dir: Path, date_str: str, prices: Optional[Dict[str, Any]] = None) -> Path:
    """Creates a daily price snapshot JSON file in cache_dir."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    price_file = cache_dir / f"{date_str}.json"
    if prices is None:
        prices = {
            "date": date_str,
            "prices": {
                "101": {
                    "Normal": {"marketPrice": 25.00, "lowPrice": 20.00, "midPrice": 24.00, "highPrice": 30.00},
                    "Foil": {"marketPrice": 50.00, "lowPrice": 40.00, "midPrice": 48.00, "highPrice": 60.00},
                },
                "102": {
                    "Normal": {"marketPrice": 250.00, "lowPrice": 220.00, "midPrice": 245.00, "highPrice": 280.00},
                },
            },
        }
    elif "date" not in prices:
        prices = {"date": date_str, "prices": prices}
    with open(price_file, "w", encoding="utf-8") as f:
        json.dump(prices, f, indent=2)
    return price_file


def create_collection_csv(file_path: Path, rows: Optional[List[Dict[str, Any]]] = None) -> Path:
    """Creates a valid CardNexus collection CSV export file."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    if rows is None:
        rows = [
            {
                "name": "Johnny Silverhand",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "001",
                "finish": "Standard",
                "totalQtyOwned": 2,
                "notes": "2026-09-01: 2",
            },
            {
                "name": "Welcome to Night City - Beta Booster Box",
                "expansion": "Welcome to Night City - Beta",
                "printNumber": "",
                "finish": "Standard",
                "totalQtyOwned": 1,
                "notes": "2026-09-01",
            },
        ]

    fieldnames = ["name", "expansion", "printNumber", "finish", "totalQtyOwned", "notes"]
    with open(file_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return file_path


def create_receipt_file(purchase_dir: Path, filename: str, content: str) -> Path:
    """Creates a purchase receipt text file in purchase_dir."""
    purchase_dir.mkdir(parents=True, exist_ok=True)
    target = purchase_dir / filename
    target.write_text(content, encoding="utf-8")
    return target
