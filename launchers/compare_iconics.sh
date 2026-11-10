#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
python3 "$DIR/run_tracker.py" compare-iconics "$@"
rc=$?
if [ $rc -eq 0 ]; then
    REPORT=""
    if [ -f "$DIR/.latest_iconics_report" ]; then
        REPORT=$(cat "$DIR/.latest_iconics_report")
    fi
    if [ -z "$REPORT" ] || [ ! -f "$REPORT" ]; then
        REPORT="wnc_iconics_comparison.md"
        if [ ! -f "$REPORT" ] && [ -f "$DIR/wnc_iconics_comparison.md" ]; then
            REPORT="$DIR/wnc_iconics_comparison.md"
        fi
    fi
    if [ -n "$REPORT" ] && [ -f "$REPORT" ]; then
        if command -v open >/dev/null 2>&1; then
            open "$REPORT"
        elif command -v xdg-open >/dev/null 2>&1; then
            xdg-open "$REPORT"
        fi
    fi
fi
if [ $# -eq 0 ]; then
    read -r -p "Press Enter to continue..."
fi
exit $rc
