"""
Configuration loader for Cyberpunk TCG Tracker.
Decouples file paths from code using config.json, environment variables, or CLI overrides.
"""

import datetime
from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Optional


@dataclass
class TrackerConfig:
    collection_csv: str = "data/active_collection.csv"
    database_path: str = "data/price_history.db"
    price_cache_dir: str = "prices"
    output_report: str = "LATEST_PORTFOLIO_SUMMARY.md"
    backup_dir: str = "data/backups"


def resolve_collection_file(target_path: str) -> str:
    """
    Resolves the collection CSV:
    - Returns target_path if it is an existing file.
    - If a directory, finds the newest *.csv by mtime (ignoring backups).
    - If non-existent but parent is a directory, scans parent for newest *.csv.
    """
    p = Path(target_path)
    if p.is_file():
        return str(p.resolve())

    search_dir = p if p.is_dir() else (p.parent if p.parent.is_dir() else None)
    if search_dir:
        csv_candidates = [
            f for f in search_dir.glob("*.csv")
            if f.is_file() and "backup" not in f.name.lower() and "backup" not in f.parent.name.lower()
        ]
        if csv_candidates:
            csv_candidates.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            chosen = csv_candidates[0]
            mtime_str = datetime.datetime.fromtimestamp(chosen.stat().st_mtime).astimezone().strftime("%Y-%m-%d %H:%M:%S")
            print(f"Auto-detected newest collection CSV in '{search_dir}': {chosen.name} (modified {mtime_str})")
            return str(chosen.resolve())

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

    collection_csv = cli_overrides.get("collection_csv") or os.getenv("CYBERPUNK_COLLECTION_CSV") or cfg_data.get("collection_csv")
    database_path = cli_overrides.get("database_path") or os.getenv("CYBERPUNK_DATABASE_PATH") or cfg_data.get("database_path")
    price_cache_dir = cli_overrides.get("price_cache_dir") or os.getenv("CYBERPUNK_PRICE_CACHE_DIR") or cfg_data.get("price_cache_dir")
    output_report = cli_overrides.get("output_report") or os.getenv("CYBERPUNK_OUTPUT_REPORT") or cfg_data.get("output_report")
    backup_dir = cli_overrides.get("backup_dir") or os.getenv("CYBERPUNK_BACKUP_DIR") or cfg_data.get("backup_dir")

    return TrackerConfig(
        collection_csv=resolve_collection_file(resolve(collection_csv, "data/active_collection.csv")),
        database_path=resolve(database_path, "data/price_history.db"),
        price_cache_dir=resolve(price_cache_dir, "prices"),
        output_report=resolve(output_report, "LATEST_PORTFOLIO_SUMMARY.md"),
        backup_dir=resolve(backup_dir, "data/backups"),
    )
