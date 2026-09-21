"""
Main CLI entry point for Cyberpunk TCG Historical Price & Portfolio Tracker.
Coordinates market sync, collection intake, portfolio valuation, and report generation.
"""

import argparse
import csv
import datetime
import json
import os
from pathlib import Path
import shutil
import sys

from tracker.config import load_config
from tracker.sync import sync_market_prices, backfill_market_prices
from tracker.valuation import calculate_portfolio_valuation
from tracker.report import generate_portfolio_report
from tracker.validation import (
    validate_collection_file,
    validate_sealed_file,
    format_validation_report,
    CollectionValidationError,
)
from tracker.cardnexus import sync_cardnexus_collection


def import_collection_file(source_path: str, target_path: str, backup_dir: str) -> bool:
    is_valid, errors, rows = validate_collection_file(source_path)
    if not is_valid:
        print(f"\nError: Collection file validation failed for '{source_path}':\n", file=sys.stderr)
        print(format_validation_report(errors), file=sys.stderr)
        return False

    total_items = len(rows)
    card_items = sum(1 for r in rows if r.get("item_type") != "Sealed")
    sealed_items = sum(1 for r in rows if r.get("item_type") == "Sealed")
    total_qty = sum(int(r.get("totalQtyOwned", 1)) for r in rows)

    os.makedirs(backup_dir, exist_ok=True)
    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)

    if os.path.exists(target_path):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(backup_dir, f"active_collection_{timestamp}.csv")
        shutil.copyfile(target_path, backup_file)
        print(f"Backed up existing collection to {backup_file}")

    shutil.copyfile(source_path, target_path)
    sealed_info = f" and {sealed_items} sealed items" if sealed_items else ""
    print(f"Successfully imported active collection: {total_items} entries ({card_items} cards{sealed_info}, {total_qty} physical units).")
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
    parser.add_argument("--date", dest="report_date", help="Optional specific snapshot date (YYYY-MM-DD) for report generation")
    parser.add_argument("--live", action="store_true", help="Scrape live market prices from TCGCSV instead of using cached or remote daily snapshots")
    parser.add_argument("--sync-collection", action="store_true", help="Sync collection directly from CardNexus API before running valuation")
    parser.add_argument("--refresh-catalog", action="store_true", help="Force fresh download of CardNexus catalogue feed (bypasses 24h cache)")
    parser.add_argument("--include-marketplace", action=argparse.BooleanOptionalAction, default=True, help="Include cards listed for sale on CardNexus Marketplace (default: True)")

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
        generate_portfolio_report(
            db_path=cfg.database_path,
            output_md=cfg.output_report,
            price_cache_dir=cfg.price_cache_dir,
            target_date=args.report_date,
        )
        return

    if args.import_path:
        success = import_collection_file(
            source_path=args.import_path,
            target_path=cfg.collection_csv,
            backup_dir=cfg.backup_dir,
        )
        if not success:
            sys.exit(1)

    if args.sync_collection:
        print("\n--- Synchronizing Collection from CardNexus API ---")
        target_path = os.path.join(cfg.collection_csv, "active_collection.csv") if os.path.isdir(cfg.collection_csv) else cfg.collection_csv
        success, snapshot_file, total_units = sync_cardnexus_collection(
            target_csv=target_path,
            backup_dir=cfg.backup_dir,
            api_key=cfg.cardnexus_api_key,
            include_marketplace=args.include_marketplace,
            refresh_catalog=args.refresh_catalog,
        )
        if not success:
            sys.exit(1)
        if os.path.isdir(cfg.collection_csv):
            cfg.collection_csv = target_path

    sealed_rows = []
    if cfg.sealed_csv and os.path.isfile(cfg.sealed_csv):
        is_sealed_valid, sealed_errors, sealed_rows = validate_sealed_file(cfg.sealed_csv)
        if not is_sealed_valid:
            print(f"\nError: Sealed inventory validation failed for '{cfg.sealed_csv}':\n", file=sys.stderr)
            print(format_validation_report(sealed_errors), file=sys.stderr)
            sys.exit(1)

    if args.backfill_date:
        date_str = args.backfill_date
        print(f"\n--- Backfilling Cyberpunk TCG Market Data for {date_str} ---")
        is_valid, validation_errors, collection_rows = validate_collection_file(cfg.collection_csv)
        if not is_valid:
            print(f"\nError: Collection validation failed for '{cfg.collection_csv}':\n", file=sys.stderr)
            print(format_validation_report(validation_errors), file=sys.stderr)
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
        generate_portfolio_report(
            db_path=cfg.database_path,
            output_md=cfg.output_report,
            price_cache_dir=cfg.price_cache_dir,
            target_date=date_str,
        )
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

    if cfg.sealed_csv and os.path.isfile(cfg.sealed_csv):
        print(f"Sealed source: {Path(cfg.sealed_csv).name} ({len(sealed_rows)} items)")

    is_valid, validation_errors, collection_rows = validate_collection_file(cfg.collection_csv)
    if not is_valid:
        print(f"\nError: Collection validation failed for '{cfg.collection_csv}':\n", file=sys.stderr)
        print(format_validation_report(validation_errors), file=sys.stderr)
        sys.exit(1)

    cards_count = sum(1 for r in collection_rows if r.get("item_type") != "Sealed")
    sealed_count = sum(1 for r in collection_rows if r.get("item_type") == "Sealed")
    if sealed_count > 0:
        print(f"Discovered {len(collection_rows)} collection entries: {cards_count} card entries and {sealed_count} sealed items.")

    price_file = sync_market_prices(price_dir=cfg.price_cache_dir, target_date=today, force=args.force, live=args.live)

    effective_date = today
    if os.path.isfile(price_file):
        try:
            with open(price_file, "r", encoding="utf-8") as f:
                p_data = json.load(f)
                file_date = p_data.get("date")
                if file_date:
                    effective_date = file_date
        except Exception:
            pass

    try:
        calculate_portfolio_valuation(
            date_str=effective_date,
            collection_path=cfg.collection_csv,
            cache_dir=cfg.price_cache_dir,
            db_path=cfg.database_path,
            force=args.force,
            collection_rows=collection_rows,
            sealed_rows=sealed_rows,
            price_file=price_file,
        )
    except CollectionValidationError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)

    generate_portfolio_report(
        db_path=cfg.database_path,
        output_md=cfg.output_report,
        price_cache_dir=cfg.price_cache_dir,
        target_date=args.report_date or effective_date,
    )


if __name__ == "__main__":
    main()
