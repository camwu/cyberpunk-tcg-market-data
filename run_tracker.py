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
from tracker.validation import validate_collection_file, validate_sealed_file, CollectionValidationError


def import_collection_file(source_path: str, target_path: str, backup_dir: str) -> bool:
    is_valid, errors, rows = validate_collection_file(source_path)
    if not is_valid:
        print(f"Error: Collection file validation failed for '{source_path}':", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return False

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
    parser.add_argument("--sealed", dest="sealed_csv", help="Path to sealed inventory CSV file")
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
        sealed_csv=args.sealed_csv,
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

    sealed_rows = []
    if cfg.sealed_csv:
        is_sealed_valid, sealed_errors, sealed_rows = validate_sealed_file(cfg.sealed_csv)
        if not is_sealed_valid:
            print(f"\nError: Sealed inventory validation failed for '{cfg.sealed_csv}':", file=sys.stderr)
            for err in sealed_errors:
                print(f"  - {err}", file=sys.stderr)
            sys.exit(1)

    if args.backfill_date:
        date_str = args.backfill_date
        print(f"\n--- Backfilling Cyberpunk TCG Market Data for {date_str} ---")
        is_valid, validation_errors, collection_rows = validate_collection_file(cfg.collection_csv)
        if not is_valid:
            print(f"\nError: Collection validation failed for '{cfg.collection_csv}':", file=sys.stderr)
            for err in validation_errors:
                print(f"  - {err}", file=sys.stderr)
            sys.exit(1)

        backfill_market_prices(date_str, price_dir=cfg.price_cache_dir)
        try:
            calculate_portfolio_valuation(
                date_str=date_str,
                collection_path=cfg.collection_csv,
                cache_dir=cfg.price_cache_dir,
                db_path=cfg.database_path,
                force=args.force,
                collection_rows=collection_rows,
                sealed_rows=sealed_rows,
            )
        except CollectionValidationError as e:
            print(f"\nError: {e}", file=sys.stderr)
            sys.exit(1)
        generate_portfolio_report(db_path=cfg.database_path, output_md=cfg.output_report)
        return

    # Standard run for today
    today = datetime.datetime.now().astimezone().strftime("%Y-%m-%d")
    print(f"\n--- Running Cyberpunk TCG Valuation Pipeline ({today}) ---")
    if os.path.isfile(cfg.collection_csv):
        mtime_str = datetime.datetime.fromtimestamp(os.path.getmtime(cfg.collection_csv)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        print(f"Collection source: {Path(cfg.collection_csv).name} (modified {mtime_str})")
        print(f"Full path: {cfg.collection_csv}")
    else:
        print(f"Collection source: {cfg.collection_csv}")

    if cfg.sealed_csv:
        print(f"Sealed source: {Path(cfg.sealed_csv).name} ({len(sealed_rows)} items)")

    is_valid, validation_errors, collection_rows = validate_collection_file(cfg.collection_csv)
    if not is_valid:
        print(f"\nError: Collection validation failed for '{cfg.collection_csv}':", file=sys.stderr)
        for err in validation_errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    sync_market_prices(price_dir=cfg.price_cache_dir, target_date=today, force=args.force)

    try:
        calculate_portfolio_valuation(
            date_str=today,
            collection_path=cfg.collection_csv,
            cache_dir=cfg.price_cache_dir,
            db_path=cfg.database_path,
            force=args.force,
            collection_rows=collection_rows,
            sealed_rows=sealed_rows,
        )
    except CollectionValidationError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)

    generate_portfolio_report(db_path=cfg.database_path, output_md=cfg.output_report)


if __name__ == "__main__":
    main()
