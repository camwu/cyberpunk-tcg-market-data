"""
Main CLI entry point for Cyberpunk TCG Historical Price & Portfolio Tracker.
Coordinates market sync, collection intake, portfolio valuation, and report generation.
"""

import argparse
import csv
import datetime
import os
from pathlib import Path
import shutil
import sys

from tracker.config import load_config
from tracker.sync import sync_market_prices, backfill_market_prices
from tracker.valuation import calculate_portfolio_valuation
from tracker.report import generate_portfolio_report


def import_collection_file(source_path: str, target_path: str, backup_dir: str) -> bool:
    if not os.path.exists(source_path):
        print(f"Error: Source collection file not found at '{source_path}'", file=sys.stderr)
        return False

    required_cols = {"totalQtyOwned", "name", "printNumber", "finish", "expansion"}
    with open(source_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        headers = set(reader.fieldnames or [])
        missing = required_cols - headers
        if missing:
            print(f"Error: Missing required CardNexus columns: {missing}", file=sys.stderr)
            return False

        rows = list(reader)
        total_items = len(rows)
        total_qty = sum(int(r.get("totalQtyOwned", 1)) for r in rows)

    os.makedirs(backup_dir, exist_ok=True)
    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)

    if os.path.exists(target_path):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(backup_dir, f"active_collection_{timestamp}.csv")
        shutil.copyfile(target_path, backup_file)
        print(f"Backed up existing collection to {backup_file}")

    shutil.copyfile(source_path, target_path)
    print(f"Successfully imported active collection: {total_items} card entries ({total_qty} physical copies).")
    return True


def main():
    parser = argparse.ArgumentParser(description="Cyberpunk TCG Portfolio & Market Price Tracker")
    parser.add_argument("collection_target", nargs="?", default=None, help="Optional direct path to CSV file or directory (supports drag-and-drop)")
    parser.add_argument("--config", dest="config_path", help="Path to custom JSON configuration file")
    parser.add_argument("--collection", dest="collection_csv", help="Path to active collection CSV file")
    parser.add_argument("--db", dest="database_path", help="Path to SQLite historical database")
    parser.add_argument("--prices", dest="price_cache_dir", help="Path to daily price cache directory")
    parser.add_argument("--output", dest="output_report", help="Path to markdown output report")
    parser.add_argument("--import-file", "--import", dest="import_path", help="Path to new CardNexus CSV export to import")
    parser.add_argument("--force", action="store_true", help="Force fresh price sync and recalculate today's valuation")
    parser.add_argument("--backfill", dest="backfill_date", help="Backfill historical prices for YYYY-MM-DD from archive")
    parser.add_argument("--report-only", action="store_true", help="Display latest portfolio report without syncing or calculating")

    args = parser.parse_args()

    # Load configuration
    collection_arg = args.collection_target or args.collection_csv
    cfg = load_config(
        config_path=args.config_path,
        collection_csv=collection_arg,
        database_path=args.database_path,
        price_cache_dir=args.price_cache_dir,
        output_report=args.output_report,
    )

    if args.report_only:
        generate_portfolio_report(db_path=cfg.database_path, output_md=cfg.output_report)
        return

    if args.import_path:
        success = import_collection_file(
            source_path=args.import_path,
            target_path=cfg.collection_csv,
            backup_dir=cfg.backup_dir,
        )
        if not success:
            sys.exit(1)

    if args.backfill_date:
        date_str = args.backfill_date
        print(f"\n--- Backfilling Cyberpunk TCG Market Data for {date_str} ---")
        backfill_market_prices(date_str, price_dir=cfg.price_cache_dir)
        calculate_portfolio_valuation(
            date_str=date_str,
            collection_path=cfg.collection_csv,
            cache_dir=cfg.price_cache_dir,
            db_path=cfg.database_path,
            force=args.force,
        )
        generate_portfolio_report(db_path=cfg.database_path, output_md=cfg.output_report)
        return

    # Standard run for today
    today = datetime.datetime.now().astimezone().strftime("%Y-%m-%d")
    print(f"\n--- Running Cyberpunk TCG Valuation Pipeline ({today}) ---")
    print(f"Collection source: {cfg.collection_csv}")

    sync_market_prices(price_dir=cfg.price_cache_dir, target_date=today, force=args.force)

    calculate_portfolio_valuation(
        date_str=today,
        collection_path=cfg.collection_csv,
        cache_dir=cfg.price_cache_dir,
        db_path=cfg.database_path,
        force=args.force,
    )

    generate_portfolio_report(db_path=cfg.database_path, output_md=cfg.output_report)


if __name__ == "__main__":
    main()
