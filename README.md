# Cyberpunk TCG Market Data & Portfolio Tracker

Automated daily market price scraper and collection portfolio valuation tracker for the Cyberpunk Trading Card Game (TCGplayer Category 92).

This repository contains **zero personal collection data** and operates both as a public market price archive and an open-source valuation engine.

---

## Features

- **Daily Cloud Scraper**: GitHub Actions workflow (`.github/workflows/daily_sync.yml`) runs daily at 20:30 UTC, saving price snapshots under `prices/`.
- **Decoupled Architecture**: Personal collection data, SQLite valuation databases, and portfolio reports remain strictly local and private.
- **Configurable Storage**: Point the engine to any local directory or CardNexus CSV export via `config.json` or CLI flags.
- **Rarity & Finish Tracking**: Tracks portfolio distribution across official geometric rarity tiers (`∧ Common`, `∨ Uncommon`, `◇ Rare`, `◈ Epic`, `◆ Secret`, `★ Iconic`, `▣ Nova`) and finishes.
- **Color Indicators**: Categorizes holdings across all 4 card colors (`🟢 Green`, `🔵 Blue`, `🔴 Red`, `🟡 Yellow`).
- **Historical Performance**: Computes rolling L7D price deltas and lifetime gain/loss against baseline acquisition prices.

---

## Quick Start

### 1. Daily Market Price Scraping (Standalone)
To scrape current TCGplayer market prices for Category 92:
```bash
python scrape.py
```
Output files are saved under `prices/YYYY-MM-DD.json` and `prices/latest.json`.

---

### 2. Personal Collection Portfolio Tracking

To track your private collection:

1. Copy `config.example.json` to `config.json` (gitignored):
   ```bash
   cp config.example.json config.json
   ```
2. Edit `config.json` to point to your collection CSV and SQLite database paths:
   ```json
   {
     "collection_csv": "path/to/active_collection.csv",
     "database_path": "path/to/price_history.db",
     "price_cache_dir": "prices",
     "output_report": "path/to/LATEST_PORTFOLIO_SUMMARY.md",
     "backup_dir": "path/to/backups"
   }
   ```
3. Run the tracker:
   ```bash
   python run_tracker.py
   ```

---

## CLI Options

```bash
# Run standard daily sync, valuation, and report generation
python run_tracker.py

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
