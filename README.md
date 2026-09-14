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
Output files are saved under `prices/YYYY-MM-DD.json` and `prices/latest.json`.

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

## CLI Options

```bash
# Run standard daily sync, valuation, and report generation (auto-detects newest CSV in data/)
python run_tracker.py

# Pass a specific CSV export or directory directly (supports drag-and-drop)
python run_tracker.py path/to/my_cards.csv

# Display latest report without syncing or recalculating
python run_tracker.py --report-only

# Force re-sync of today's prices and overwrite snapshot
python run_tracker.py --force

# Import new CardNexus CSV export (automatically creates timestamped backup)
python run_tracker.py --import path/to/inventory.csv

# Backfill historical prices from TCGCSV archives (requires 7-Zip)
python run_tracker.py --backfill 2026-09-11

# Use custom configuration file
python run_tracker.py --config my_config.json
```
