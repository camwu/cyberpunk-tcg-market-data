#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [ -d "$DIR/.git" ] || [ -f "$DIR/.git" ]; then
    # Best-effort fast-forward; ignore exit code if offline, detached, or dirty
    git -C "$DIR" pull --ff-only 2>/dev/null || true
fi
python3 "$DIR/run_tracker.py" compare-iconics "$@"

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
        open "$REPORT" 2>/dev/null || true
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$REPORT" 2>/dev/null || true
    fi
fi
