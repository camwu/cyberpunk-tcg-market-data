#!/usr/bin/env bash
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
python3 "$DIR/run_tracker.py" compare-iconics "$@"
rc=$?
if [ $# -eq 0 ]; then
    read -r -p "Press Enter to continue..."
fi
exit $rc
