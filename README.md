# Cyberpunk TCG Market Data & Portfolio Tracker

Automated daily market price scraper and portfolio valuation CLI tool for the Cyberpunk Trading Card Game.

---

## ✨ Features

- **Automated Daily Price Sync**: GitHub Actions workflow (`.github/workflows/daily_sync.yml`) runs daily at 20:17 UTC to update card metadata (`cards.json`) and record daily TCGplayer market prices under `prices/`.
- **Unified Portfolio Tracking & Direct Sync**: Ingests cards and sealed products (Booster Boxes, Starter Decks) via direct CardNexus Public API synchronization or offline CSV exports into a local SQLite database (`data/price_history.db`).
- **Historical Performance & ROI**: Calculates rolling 7-day price deltas and lifetime gain/loss against purchase prices or initial market baselines.
- **Rarity, Finish & Color Breakdowns**: Summarizes collection distribution across official rarity tiers (`▽ Common` through `▣ Nova Rare`), standard/foil finishes, and card colors.
- **1-Click & Drag-and-Drop Launchers**: Update portfolios via Windows batch (`update_portfolio.bat`), Unix shell (`update_portfolio.sh`), or the Python CLI with automatic CSV detection.

---

## 🚀 Quick Start

### Prerequisites
- **Python 3.9+**: Built entirely on the Python standard library with 0 external `pip` dependencies.
- **7-Zip** *(Optional)*: Required only when backfilling historical price archives via `--backfill`.

### 1. Daily Market Price Scraping (Standalone)
To scrape current market prices from TCGplayer:
```bash
python scrape.py
```
Output files are saved under `prices/YYYY-MM-DD.json` and `prices/latest.json`. If a price snapshot for today already exists, `scrape.py` automatically skips execution to preserve original timestamps. To force an overwrite, pass `--force`:
```bash
python scrape.py --force
```

---

### 2. Portfolio Valuation

#### Option A: Direct API Synchronization (Recommended)
Configure your CardNexus Public API Bearer token (`cnk_live_...` from CardNexus Settings > API Keys) via environment variable:
- **Windows (PowerShell)**:
  ```powershell
  [Environment]::SetEnvironmentVariable("CARDNEXUS_API_KEY", "<API_KEY>", "User")
  ```
- **macOS / Linux**:
  ```bash
  export CARDNEXUS_API_KEY="<API_KEY>"
  ```

Run the tracker with `--sync-collection`:
```bash
python run_tracker.py --sync-collection
```
This fetches active inventory lines, downloads and caches the Cyberpunk catalog feed, validates snapshot schema and lot integrity, and promotes the validated data directly to `data/active_collection.csv` while automatically cleaning up temporary staging files. If `CARDNEXUS_API_KEY` is not configured, the tracker logs a diagnostic warning and falls back to offline collection CSV ingestion.

#### Option B: Offline CSV Export
1. **Add your collection CSV**:
   Place a CardNexus CSV export directly into the `data/` directory (e.g. `data/my_collection.csv`). The pipeline ingests both cards and sealed products (e.g. Booster Boxes, Starter Decks) from a unified CardNexus export.
   - **Sealed Products**: Identified by product catalog matching and naming conventions; `printNumber` is optional for sealed items.
   - **Acquisition Dates & Multi-Lot Entries**: Extracted from the CardNexus `notes` field. Multiple purchase dates are recorded as semicolon-delimited lots (e.g. `2026-09-02: 1; 2026-09-18: 2`). In multi-lot lists, dates lacking an explicit `: <qty>` default to 1 unit (e.g. `2026-09-02; 2026-09-18: 2` assigns 1 unit to 2026-09-02). If only a single date appears without semicolons, the entire `totalQtyOwned` is attributed to that date. The sum of parsed lots must strictly equal `totalQtyOwned`; any mismatch raises a validation error displaying an aligned 6-column CLI table and exporting failing entries to `data/validation_errors.csv` for single-pass resolution in CardNexus. If an acquisition date predates the earliest available price history, the baseline clamps to the oldest market price date. If the `notes` field omits an acquisition date entirely, the database defaults the item's acquisition date to the snapshot date when it first appears in an imported collection CSV.
   - **Auto-Discovery**: The pipeline validates required columns (`name`, `expansion`, `printNumber`, `finish`, `totalQtyOwned`) and automatically selects the newest CSV by modification timestamp.

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
     "output_report": "LATEST_PORTFOLIO_SUMMARY.md"
   }
   ```

---

### 3. Running Tests
To run the test suite:
```bash
python -m unittest discover tests -v
```

---

## ⚙️ CLI Options & Arguments

### Positional Arguments
- `collection_target`: Optional direct path to a CardNexus CSV export or directory containing collection files (supports drag-and-drop onto the terminal or launcher).

### Named Parameters & Flags
- `--config <path>`: Path to custom JSON configuration file (e.g. `config.json`).
- `--collection <path>`: Path to active collection CSV file containing cards and sealed products (overrides configuration file).
- `--sealed <path>`: Optional / legacy path to separate sealed inventory CSV file (overrides configuration file).
- `--db <path>`: Path to SQLite historical database (default: `data/price_history.db`).
- `--prices <path>`: Path to daily price cache directory (default: `prices`).
- `--output <path>`: Path to markdown summary report (default: `LATEST_PORTFOLIO_SUMMARY.md`).
- `--import <path>`, `--import-file <path>`: Validate and promote a new CardNexus CSV export to the active collection.
- `--live`: Scrape live market prices directly from TCGCSV endpoints instead of using cached local files or remote GitHub snapshots. When omitted and today's remote snapshot has not yet been published (daily cloud sync runs at 20:17 UTC, subject to standard GitHub Actions queue latency of up to 3 hours during peak load), the pipeline falls back cleanly to `prices/latest.json` with an informational notice.
- `--force`: Force a fresh price sync and recalculate/overwrite the portfolio valuation snapshot for the target date.
- `--backfill <YYYY-MM-DD>`: Backfill historical market prices from TCGCSV archive bundles (requires 7-Zip).
- `--report-only`: Render the markdown portfolio report from existing database records without syncing prices or running valuation calculations.
- `--date <YYYY-MM-DD>`: Generate the portfolio report for a specific historical snapshot date.
- `--sync-collection`: Synchronize collection directly from the CardNexus Public API using the authenticated `CARDNEXUS_API_KEY` environment variable before running valuation.
- `--refresh-catalog`: Force an immediate fresh download of the CardNexus Cyberpunk catalog feed (bypasses 24-hour local cache).
- `--include-marketplace`, `--no-include-marketplace`: Control whether active CardNexus Marketplace listings are included alongside collection cards (default: `--include-marketplace`).

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

# Import and promote a new CardNexus export directly
python run_tracker.py --import path/to/export.csv

# Backfill historical prices for an archive date
python run_tracker.py --backfill 2026-09-11

# Synchronize collection directly from CardNexus API
python run_tracker.py --sync-collection

# Synchronize collection and force fresh catalog feed download
python run_tracker.py --sync-collection --refresh-catalog

# Synchronize collection excluding cards actively listed on CardNexus Marketplace
python run_tracker.py --sync-collection --no-include-marketplace

# Use custom configuration file
python run_tracker.py --config custom_config.json
```

---

## ⚖️ License & Disclaimer

This project is an unofficial tool and is not affiliated with, endorsed by, or sponsored by CD PROJEKT RED or Weird Co. *Cyberpunk* and *Cyberpunk 2077* are registered trademarks and copyright of CD PROJEKT S.A., the Cyberpunk Trading Card Game is produced by Weird Co. under license, and all card names, images, and game assets are the property of their respective owners.

The software and documentation in this repository are licensed under the [GNU General Public License v3.0](COPYING).
