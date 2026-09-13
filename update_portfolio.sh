#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"
python3 "$DIR/run_tracker.py" "$@"

if [ -f "$DIR/LATEST_PORTFOLIO_SUMMARY.md" ]; then
    if command -v open >/dev/null 2>&1; then
        open "$DIR/LATEST_PORTFOLIO_SUMMARY.md"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$DIR/LATEST_PORTFOLIO_SUMMARY.md"
    fi
fi
