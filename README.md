# Cyberpunk TCG Market Price Scraper (Cloud Runner)

This standalone directory is designed to be hosted in its own GitHub repository (public or private) to collect daily TCGplayer market prices for the Cyberpunk Trading Card Game via TCGCSV.

It contains **zero personal collection data** and operates purely as a public market data feed.

## Setup Instructions

1. Create a new repository on GitHub (e.g. `cyberpunk-tcg-market-data`).
2. In this `cloud_scraper` directory, run:
   ```bash
   git init
   git remote add origin https://github.com/<your-username>/<repo-name>.git
   git add .
   git commit -m "Initialize market price scraper"
   git branch -M main
   git push -u origin main
   ```
3. The GitHub Actions workflow (`.github/workflows/daily_sync.yml`) will automatically run every day at 20:30 UTC and save updated JSON price snapshots under `prices/`.
