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
import time
from typing import Any, List, NamedTuple, Optional

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
from tracker.purchases import sync_purchase_history, get_purchase_history_updated_at
from tracker.constants import CACHE_TTL_24H_SECONDS


def import_collection_file(source_path: str, target_path: str) -> bool:
    is_valid, errors, rows = validate_collection_file(source_path)
    if not is_valid:
        print(f"\nError: Collection file validation failed for '{source_path}':\n", file=sys.stderr)
        print(format_validation_report(errors), file=sys.stderr)
        return False

    total_items = len(rows)
    card_items = sum(1 for r in rows if r.get("item_type") != "Sealed")
    sealed_items = sum(1 for r in rows if r.get("item_type") == "Sealed")
    total_qty = sum(int(r.get("totalQtyOwned", 1)) for r in rows)

    os.makedirs(os.path.dirname(target_path) or ".", exist_ok=True)
    shutil.copyfile(source_path, target_path)
    sealed_info = f" and {sealed_items} sealed items" if sealed_items else ""
    print(f"Successfully imported active collection: {total_items} entries ({card_items} cards{sealed_info}, {total_qty} physical units).")
    return True


class CollectionSyncResult(NamedTuple):
    collection_path: str
    collection_source: str
    collection_updated: bool


def build_tracker_argument_parser() -> argparse.ArgumentParser:
    """Builds and returns the CLI argument parser for run_tracker."""
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
    parser.add_argument(
        "--sync-collection",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Sync collection directly from CardNexus API before running valuation (default: auto-sync if API key present with 24h cache)",
    )
    parser.add_argument("--refresh-catalog", action="store_true", help="Force fresh download of CardNexus catalog feed (bypasses 24h cache)")
    parser.add_argument("--include-marketplace", action=argparse.BooleanOptionalAction, default=True, help="Include cards listed for sale on CardNexus Marketplace (default: True)")
    parser.add_argument("--purchase-dir", dest="purchase_history_dir", help="Path to purchase history receipts directory")
    parser.add_argument("--purchase-ledger", dest="purchase_history_ledger", help="Path to purchase history CSV ledger")
    parser.add_argument("--purchase-cache", dest="purchase_history_cache", help="Path to purchase history cache JSON")
    parser.add_argument(
        "--reparse-purchases",
        action="store_true",
        help="Force re-parsing of purchase history documents, bypassing SHA-256 cache and updating ledger",
    )
    return parser


def resolve_price_snapshot_date(price_file: str, fallback_date: str) -> str:
    """Reads effective snapshot date from a price JSON file, falling back to target date."""
    if os.path.isfile(price_file):
        try:
            with open(price_file, "r", encoding="utf-8") as f:
                p_data = json.load(f)
                if isinstance(p_data, dict):
                    file_date = p_data.get("date")
                    if file_date:
                        return file_date
        except (OSError, json.JSONDecodeError):
            pass
    return fallback_date


def resolve_and_sync_collection(args: argparse.Namespace, cfg: Any) -> CollectionSyncResult:
    """Resolves active collection source via import, API sync (with 24h cache), or offline CSV."""
    collection_updated = False
    collection_source = None

    if args.import_path:
        success = import_collection_file(
            source_path=args.import_path,
            target_path=cfg.collection_csv,
        )
        if not success:
            sys.exit(1)
        collection_source = f"CSV ({Path(args.import_path).name})"
        collection_updated = True

    if not collection_source:
        if args.sync_collection is False:
            pass
        elif not cfg.cardnexus_api_key:
            if args.sync_collection is True:
                print(
                    "Warning: CARDNEXUS_API_KEY is not configured. Falling back to offline collection CSV ingestion.",
                    file=sys.stderr,
                )
        else:
            if args.collection_csv or args.collection_target:
                explicit_target = args.collection_csv or args.collection_target
                target_dir = explicit_target if os.path.isdir(explicit_target) else (os.path.dirname(explicit_target) or "data")
                target_path = os.path.join(target_dir, "active_collection.csv")
            elif os.path.isdir(cfg.collection_csv):
                target_path = os.path.join(cfg.collection_csv, "active_collection.csv")
            else:
                collection_dir = os.path.dirname(cfg.collection_csv) or "data"
                target_path = os.path.join(collection_dir, "active_collection.csv")

            cache_valid = False
            file_age_seconds = None
            if os.path.isfile(target_path):
                file_age_seconds = time.time() - os.path.getmtime(target_path)
                if file_age_seconds < CACHE_TTL_24H_SECONDS:
                    cache_valid = True

            if cache_valid and args.sync_collection is not True:
                age_hours = (file_age_seconds / 3600.0) if file_age_seconds is not None else 0.0
                print(f"\nUsing cached CardNexus collection ({age_hours:.1f}h old). Use --sync-collection to refresh.")
                cfg.collection_csv = target_path
                collection_source = "CardNexus API (cached)"
            else:
                print("\n--- Synchronizing Collection from CardNexus API ---")
                try:
                    success, promoted_file, total_units = sync_cardnexus_collection(
                        target_csv=target_path,
                        api_key=cfg.cardnexus_api_key,
                        include_marketplace=args.include_marketplace,
                        refresh_catalog=args.refresh_catalog,
                    )
                except CollectionValidationError:
                    sys.exit(1)
                if success:
                    cfg.collection_csv = target_path
                    collection_source = "CardNexus API"
                    collection_updated = True
                else:
                    fallback_path = target_path if os.path.isfile(target_path) else (cfg.collection_csv if os.path.isfile(cfg.collection_csv) else None)
                    if fallback_path:
                        print(
                            f"Warning: CardNexus API sync failed. Falling back to cached collection: '{fallback_path}'.",
                            file=sys.stderr,
                        )
                        cfg.collection_csv = fallback_path
                        collection_source = "CardNexus API (fallback)"
                    else:
                        print(
                            f"Error: CardNexus API sync failed and no collection file exists.",
                            file=sys.stderr,
                        )
                        sys.exit(1)

    if not collection_source:
        collection_source = f"CSV ({Path(cfg.collection_csv).name})"

    return CollectionSyncResult(
        collection_path=cfg.collection_csv,
        collection_source=collection_source,
        collection_updated=collection_updated,
    )


def execute_backfill(
    date_str: str,
    cfg: Any,
    collection_source: str,
    sealed_rows: List[dict],
    total_cost_basis: float,
    purchases_updated_at: Optional[str],
    force: bool,
) -> None:
    """Executes market price backfill, portfolio valuation, and report generation for a target date."""
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
            force=force,
            collection_rows=collection_rows,
            sealed_rows=sealed_rows,
            collection_source=collection_source,
            total_cost_basis=total_cost_basis,
            purchases_updated_at=purchases_updated_at,
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


def main(argv: Optional[List[str]] = None):
    parser = build_tracker_argument_parser()
    args = parser.parse_args(argv)

    cfg = load_config(
        config_path=args.config_path, collection_csv=args.collection_target or args.collection_csv,
        database_path=args.database_path, price_cache_dir=args.price_cache_dir, output_report=args.output_report,
        sealed_csv=args.sealed_csv, purchase_history_dir=args.purchase_history_dir,
        purchase_history_ledger=args.purchase_history_ledger, purchase_history_cache=args.purchase_history_cache,
    )

    if args.report_only:
        generate_portfolio_report(cfg.database_path, cfg.output_report, cfg.price_cache_dir, args.report_date)
        return

    sync_res = resolve_and_sync_collection(args, cfg)
    collection_source, collection_updated = sync_res.collection_source, sync_res.collection_updated

    sealed_rows = []
    if cfg.sealed_csv and os.path.isfile(cfg.sealed_csv):
        is_sealed_valid, sealed_errors, sealed_rows = validate_sealed_file(cfg.sealed_csv)
        if not is_sealed_valid:
            print(f"\nError: Sealed inventory validation failed for '{cfg.sealed_csv}':\n{format_validation_report(sealed_errors)}", file=sys.stderr)
            sys.exit(1)

    total_cost_basis, _, purchases_updated = sync_purchase_history(
        purchase_dir=cfg.purchase_history_dir, cache_path=cfg.purchase_history_cache,
        ledger_path=cfg.purchase_history_ledger, reparse=args.reparse_purchases,
    )
    purchases_updated_at = get_purchase_history_updated_at(cfg.purchase_history_cache) if total_cost_basis > 0 else None

    if args.backfill_date:
        execute_backfill(
            args.backfill_date, cfg, collection_source, sealed_rows, total_cost_basis, purchases_updated_at,
            force=args.force or args.reparse_purchases,
        )
        return
    today = datetime.datetime.now().astimezone().strftime("%Y-%m-%d")
    print(f"\n--- Running Cyberpunk TCG Valuation Pipeline ({today}) ---")
    if os.path.isfile(cfg.collection_csv):
        mtime_str = datetime.datetime.fromtimestamp(os.path.getmtime(cfg.collection_csv)).astimezone().strftime("%Y-%m-%d %H:%M:%S")
        print(f"Collection source: {collection_source} (modified {mtime_str})\nFull path: {cfg.collection_csv}")
    else:
        print(f"Collection source: {collection_source}")

    if cfg.sealed_csv and os.path.isfile(cfg.sealed_csv):
        print(f"Sealed source: {Path(cfg.sealed_csv).name} ({len(sealed_rows)} items)")

    is_valid, validation_errors, collection_rows = validate_collection_file(cfg.collection_csv)
    if not is_valid:
        print(f"\nError: Collection validation failed for '{cfg.collection_csv}':\n{format_validation_report(validation_errors)}", file=sys.stderr)
        sys.exit(1)

    sealed_count = sum(1 for r in collection_rows if r.get("item_type") == "Sealed")
    if sealed_count > 0:
        cards_count = len(collection_rows) - sealed_count
        print(f"Discovered {len(collection_rows)} collection entries: {cards_count} card entries and {sealed_count} sealed items.")

    price_file = sync_market_prices(price_dir=cfg.price_cache_dir, target_date=today, force=args.force, live=args.live)
    effective_date = resolve_price_snapshot_date(price_file, today)
    force_val = args.force or collection_updated or (args.sync_collection is True) or args.reparse_purchases or purchases_updated

    try:
        calculate_portfolio_valuation(
            date_str=effective_date, collection_path=cfg.collection_csv, cache_dir=cfg.price_cache_dir,
            db_path=cfg.database_path, force=force_val, collection_rows=collection_rows, sealed_rows=sealed_rows,
            price_file=price_file, collection_source=collection_source, total_cost_basis=total_cost_basis,
            purchases_updated_at=purchases_updated_at,
        )
    except CollectionValidationError as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)

    generate_portfolio_report(cfg.database_path, cfg.output_report, cfg.price_cache_dir, args.report_date or effective_date)


if __name__ == "__main__":
    main()

