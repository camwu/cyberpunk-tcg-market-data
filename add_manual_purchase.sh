#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"
echo "================================================================"
echo "  CYBERPUNK TCG - RECORD MANUAL PURCHASE (NO RECEIPT)"
echo "================================================================"
python3 "$DIR/run_tracker.py" add-manual "$@"
