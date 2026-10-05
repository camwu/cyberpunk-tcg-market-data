#!/usr/bin/env bash
set -e

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}" )" && pwd)"
echo "================================================================"
echo "  CYBERPUNK TCG - RECORD CASH PURCHASE (NO RECEIPT)"
echo "================================================================"
python3 "$DIR/run_tracker.py" add-cash "$@"
