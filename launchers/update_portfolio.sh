#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
echo "================================================================"
echo "  CYBERPUNK TCG - UPDATING PORTFOLIO AND MARKET PRICES"
echo "================================================================"
if [ -d "$DIR/.git" ] || [ -f "$DIR/.git" ]; then
    # Best-effort fast-forward; ignore exit code if offline, detached, or dirty
    git -C "$DIR" pull --ff-only 2>/dev/null || true
fi
python3 "$DIR/run_tracker.py" "$@"

REPORT=""
if [ -f "$DIR/.latest_report" ]; then
    REPORT=$(cat "$DIR/.latest_report")
fi
if [ -z "$REPORT" ] || [ ! -f "$REPORT" ]; then
    REPORT="portfolio_summary.md"
    if [ ! -f "$REPORT" ] && [ -f "$DIR/portfolio_summary.md" ]; then
        REPORT="$DIR/portfolio_summary.md"
    fi
fi

if [ -n "$REPORT" ] && [ -f "$REPORT" ]; then
    if command -v open >/dev/null 2>&1; then
        open "$REPORT"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$REPORT"
    fi
fi
