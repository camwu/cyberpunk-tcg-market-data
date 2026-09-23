"""
Configuration loader for Cyberpunk TCG Tracker.
Decouples file paths from code using config.json, environment variables, or CLI overrides.
"""

import datetime
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PRICES_DIR = str((REPO_ROOT / "prices").resolve())


@dataclass
class TrackerConfig:
    collection_csv: str = "data"
    database_path: str = "data/price_history.db"
    price_cache_dir: str = DEFAULT_PRICES_DIR
    output_report: str = "LATEST_PORTFOLIO_SUMMARY.md"
    sealed_csv: Optional[str] = None
    cardnexus_api_key: Optional[str] = None
    purchase_history_dir: Optional[str] = None
    purchase_history_ledger: Optional[str] = None
    purchase_history_cache: Optional[str] = None


def resolve_collection_file(target_path: str, is_explicit_file: bool = False) -> str:
    """
    Resolves the collection CSV path:
    - If is_explicit_file is True and target_path is an existing file, returns it directly.
    - If target_path is a directory (or default active_collection.csv), scans for all *.csv files
      (excluding backups) and selects the newest by mtime.
    - If target_path does not exist but parent directory exists, scans parent for newest *.csv.
    - If target_path is an existing file, returns it directly.
    - Otherwise, returns target_path as-is.
    """
    p = Path(target_path)

    if is_explicit_file and p.is_file():
        return str(p.resolve())

    if p.is_dir():
        search_dir = p
    elif not is_explicit_file and p.name == "active_collection.csv" and p.parent.is_dir():
        search_dir = p.parent
    elif not p.exists() and p.parent.is_dir():
        search_dir = p.parent
    elif p.is_file():
        return str(p.resolve())
    else:
        search_dir = None

    if search_dir:
        csv_candidates = [
            f for f in search_dir.glob("*.csv")
            if f.is_file()
            and "backup" not in f.name.lower()
            and "backup" not in f.parent.name.lower()
            and "sealed" not in f.name.lower()
        ]
        if csv_candidates:
            csv_candidates.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            return str(csv_candidates[0].resolve())

    return str(p.resolve() if p.is_absolute() else p)


def load_config(config_path: Optional[str] = None, **cli_overrides) -> TrackerConfig:
    """
    Loads configuration with precedence:
    1. Direct CLI overrides
    2. Specified config file or local config.json if present
    3. Environment variables
    4. Default relative paths
    """
    cfg_data = {}
    search_paths = []

    if config_path:
        search_paths.append(Path(config_path))
    else:
        search_paths.append(Path("config.json"))
        repo_root = Path(__file__).resolve().parent.parent
        search_paths.append(repo_root / "config.json")

    found_cfg_file = None
    for p in search_paths:
        if p.is_file():
            found_cfg_file = p
            break

    if found_cfg_file:
        try:
            with open(found_cfg_file, "r", encoding="utf-8") as f:
                cfg_data = json.load(f)
        except Exception as e:
            print(f"Warning: Could not read configuration from {found_cfg_file}: {e}")

    # Resolve paths: if relative, make them relative to config file directory
    base_dir = found_cfg_file.parent if found_cfg_file else Path.cwd()

    def resolve(val: Optional[str], default: str) -> str:
        raw = val or default
        p = Path(raw)
        if not p.is_absolute() and found_cfg_file:
            return str((base_dir / p).resolve())
        return str(p)

    def resolve_repo_asset(val: Optional[str], default_rel: str) -> str:
        raw = val or default_rel
        p = Path(raw)
        if not p.is_absolute():
            if found_cfg_file:
                return str((base_dir / p).resolve())
            return str((REPO_ROOT / p).resolve())
        return str(p)

    cli_collection = cli_overrides.get("collection_csv")
    is_explicit = bool(cli_collection and Path(cli_collection).is_file())
    collection_csv = cli_collection or os.getenv("CYBERPUNK_COLLECTION_CSV") or cfg_data.get("collection_csv")
    database_path = cli_overrides.get("database_path") or os.getenv("CYBERPUNK_DATABASE_PATH") or cfg_data.get("database_path")
    price_cache_dir = cli_overrides.get("price_cache_dir") or os.getenv("CYBERPUNK_PRICE_CACHE_DIR") or cfg_data.get("price_cache_dir")
    output_report = cli_overrides.get("output_report") or os.getenv("CYBERPUNK_OUTPUT_REPORT") or cfg_data.get("output_report")

    resolved_collection = resolve_collection_file(resolve(collection_csv, "data"), is_explicit_file=is_explicit)

    cli_sealed = cli_overrides.get("sealed_csv")
    sealed_csv_val = cli_sealed or os.getenv("CYBERPUNK_SEALED_CSV") or cfg_data.get("sealed_csv")
    if sealed_csv_val:
        candidate_sealed = resolve(sealed_csv_val, "data/sealed_inventory.csv")
        resolved_sealed = candidate_sealed if os.path.isfile(candidate_sealed) else None
    else:
        adjacent_sealed = Path(resolved_collection).parent / "sealed_inventory.csv"
        resolved_sealed = str(adjacent_sealed.resolve()) if adjacent_sealed.is_file() else None

    cardnexus_key = cli_overrides.get("cardnexus_api_key") or os.getenv("CARDNEXUS_API_KEY")
    if not cardnexus_key and sys.platform == "win32":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as reg_key:
                val, _ = winreg.QueryValueEx(reg_key, "CARDNEXUS_API_KEY")
                if val:
                    cardnexus_key = str(val).strip()
        except Exception:
            pass

    cli_purchase_dir = cli_overrides.get("purchase_history_dir")
    purchase_dir_val = cli_purchase_dir or os.getenv("CYBERPUNK_PURCHASE_HISTORY_DIR") or cfg_data.get("purchase_history_dir")
    if purchase_dir_val:
        resolved_purchase_dir = resolve(purchase_dir_val, "data/purchase_history")
    else:
        adjacent_purchase_dir = Path(resolved_collection).parent / "purchase_history"
        resolved_purchase_dir = str(adjacent_purchase_dir.resolve()) if adjacent_purchase_dir.is_dir() else None

    cli_ledger = cli_overrides.get("purchase_history_ledger")
    ledger_val = cli_ledger or os.getenv("CYBERPUNK_PURCHASE_HISTORY_LEDGER") or cfg_data.get("purchase_history_ledger")
    if ledger_val:
        resolved_ledger = resolve(ledger_val, "data/purchase_history.csv")
    elif resolved_purchase_dir:
        resolved_ledger = str(Path(resolved_purchase_dir).parent / "purchase_history.csv")
    else:
        resolved_ledger = None

    cli_cache = cli_overrides.get("purchase_history_cache")
    cache_val = cli_cache or os.getenv("CYBERPUNK_PURCHASE_HISTORY_CACHE") or cfg_data.get("purchase_history_cache")
    if cache_val:
        resolved_cache = resolve(cache_val, "data/purchase_history_cache.json")
    elif resolved_purchase_dir:
        resolved_cache = str(Path(resolved_purchase_dir).parent / "purchase_history_cache.json")
    else:
        resolved_cache = None

    return TrackerConfig(
        collection_csv=resolved_collection,
        database_path=resolve(database_path, "data/price_history.db"),
        price_cache_dir=resolve_repo_asset(price_cache_dir, "prices"),
        output_report=resolve(output_report, "LATEST_PORTFOLIO_SUMMARY.md"),
        sealed_csv=resolved_sealed,
        cardnexus_api_key=cardnexus_key,
        purchase_history_dir=resolved_purchase_dir,
        purchase_history_ledger=resolved_ledger,
        purchase_history_cache=resolved_cache,
    )
