#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"
python3 "$DIR/run_tracker.py" "$@"

REPORT=""
if [ -f "$DIR/.latest_report" ]; then
    REPORT=$(cat "$DIR/.latest_report")
fi
if [ -z "$REPORT" ] || [ ! -f "$REPORT" ]; then
    REPORT="LATEST_PORTFOLIO_SUMMARY.md"
    if [ ! -f "$REPORT" ] && [ -f "$DIR/LATEST_PORTFOLIO_SUMMARY.md" ]; then
        REPORT="$DIR/LATEST_PORTFOLIO_SUMMARY.md"
    fi
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

