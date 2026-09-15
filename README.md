# Cyberpunk TCG Market Data & Portfolio Tracker

Automated daily market price scraper and collection portfolio valuation engine for the Cyberpunk Trading Card Game.

This repository provides an automated market price archive alongside a local valuation tracker for card collections.

---

## Features

- **Daily Cloud Scraper**: GitHub Actions workflow (`.github/workflows/daily_sync.yml`) runs daily at 20:17 UTC, updating `cards.json` and saving slim price snapshots under `prices/`.
- **Decoupled Architecture**: Static card metadata (`cards.json`) is separated from daily price snapshots (`prices/YYYY-MM-DD.json`), reducing snapshot payload size by over 70%.
- **Configurable Storage**: Point the engine to any local directory or CardNexus CSV export via `config.json` or CLI flags.
- **Rarity & Finish Tracking**: Tracks portfolio distribution across official geometric rarity tiers (`▽ Common`, `△ Uncommon`, `◇ Rare`, `🞚 Epic`, `⯁ Secret`, `★ Iconic`, `▣ Nova`) and finishes.
- **Color Indicators**: Categorizes holdings across all 4 card colors (`🟢 Green`, `🔵 Blue`, `🔴 Red`, `🟡 Yellow`).
- **Historical Performance**: Computes rolling L7D price deltas and lifetime gain/loss against baseline acquisition prices.

---

## Quick Start

### 1. Daily Market Price Scraping (Standalone)
To scrape current market prices from TCGplayer:
```bash
python scrape.py
```
Output files are saved under `prices/YYYY-MM-DD.json` and `prices/latest.json`. If a price snapshot for today already exists, `scrape.py` automatically skips execution to preserve original timestamps. To force an overwrite, pass `--force`:
```bash
python scrape.py prices --force
```

---

### 2. Portfolio Valuation

1. **Add your collection CSV**:
   Place a CardNexus CSV export directly into the `data/` directory (e.g. `data/my_collection.csv`). The engine validates that required columns (`name`, `expansion`, `printNumber`, `finish`, `totalQtyOwned`) are present and automatically selects the newest file by modification timestamp.

2. **Run the tracker**:
   - **Windows 1-Click / Drag-and-Drop**: Double-click `update_portfolio.bat`, or drag-and-drop any CSV file onto it.
   - **macOS / Linux Launcher**: Run `./update_portfolio.sh` (or pass/drag a CSV file: `./update_portfolio.sh path/to/cards.csv`).
   - **CLI**:
     ```bash
     python run_tracker.py
     ```

3. **(Optional) Custom Paths via `config.json`**:
   To customize locations outside the repository, copy `config.example.json` to `config.json` (gitignored):
   ```bash
   cp config.example.json config.json
   ```
   Set `collection_csv` to a directory (e.g. `"data"`) or an explicit file path:
   ```json
   {
     "collection_csv": "data",
     "database_path": "data/price_history.db",
     "price_cache_dir": "prices",
     "output_report": "LATEST_PORTFOLIO_SUMMARY.md",
     "backup_dir": "data/backups"
   }
   ```

---

### 3. Running Tests
To execute the automated unit test suite:
```bash
python -m unittest discover tests -v
```

---

## CLI Options & Arguments

### Positional Arguments
- `collection_target`: Optional direct path to a CardNexus CSV export or directory containing collection files (supports drag-and-drop onto the terminal or launcher).

### Named Parameters & Flags
- `--config <path>`: Path to custom JSON configuration file (e.g. `config.json`).
- `--collection <path>`: Path to active card collection CSV file (overrides configuration file).
- `--sealed <path>`: Path to sealed inventory CSV file (overrides configuration file).
- `--db <path>`: Path to SQLite historical database (default: `data/price_history.db`).
- `--prices <path>`: Path to daily price cache directory (default: `prices`).
- `--output <path>`: Path to markdown summary report (default: `LATEST_PORTFOLIO_SUMMARY.md`).
- `--import <path>`, `--import-file <path>`: Validate, back up, and import a new CardNexus CSV export.
- `--live`: Scrape live market prices directly from TCGCSV endpoints instead of using cached local files or remote GitHub snapshots. When omitted and today's remote snapshot has not yet been published (daily cloud sync runs at 20:17 UTC), the engine falls back cleanly to `prices/latest.json` with an informational notice.
- `--force`: Force a fresh price sync and recalculate/overwrite the portfolio valuation snapshot for the target date.
- `--backfill <YYYY-MM-DD>`: Backfill historical market prices from TCGCSV archive bundles (requires 7-Zip).
- `--report-only`: Render the markdown portfolio report from existing database records without syncing prices or running valuation calculations.
- `--date <YYYY-MM-DD>`: Generate the portfolio report for a specific historical snapshot date.

---

### Example Commands

```bash
# Standard run (auto-detects newest CSV in data/, falls back to latest prices if today is unpublished)
python run_tracker.py

# Live scrape current market prices from TCGCSV
python run_tracker.py --live

# Pass a specific collection file directly (supports drag-and-drop)
python run_tracker.py path/to/my_cards.csv

# Force re-sync of market prices and recalculate valuation
python run_tracker.py --force

# Display latest report without syncing prices or running calculations
python run_tracker.py --report-only

# Generate report for a specific snapshot date
python run_tracker.py --report-only --date 2026-09-13

# Import a new CardNexus export with automatic backup
python run_tracker.py --import path/to/export.csv

# Backfill historical prices for an archive date
python run_tracker.py --backfill 2026-09-11

# Use custom configuration file
python run_tracker.py --config custom_config.json
```
