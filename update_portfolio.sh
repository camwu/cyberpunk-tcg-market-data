#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"
python3 "$DIR/run_tracker.py" "$@"

REPORT=""
CONFIG_REPORT=$(python3 -c "from tracker.config import load_config; print(load_config().output_report)" 2>/dev/null || true)
if [ -n "$CONFIG_REPORT" ] && [ -f "$CONFIG_REPORT" ]; then
    REPORT="$CONFIG_REPORT"
elif [ -f "LATEST_PORTFOLIO_SUMMARY.md" ]; then
    REPORT="LATEST_PORTFOLIO_SUMMARY.md"
elif [ -f "$DIR/LATEST_PORTFOLIO_SUMMARY.md" ]; then
    REPORT="$DIR/LATEST_PORTFOLIO_SUMMARY.md"
fi

if [ -n "$REPORT" ] && [ -f "$REPORT" ]; then
    if command -v code >/dev/null 2>&1; then
        code -r "$REPORT" &
    elif command -v open >/dev/null 2>&1; then
        open "$REPORT"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$REPORT"
    fi
fi

