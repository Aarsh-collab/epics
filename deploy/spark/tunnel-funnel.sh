#!/bin/bash
# Configure only the approved EPICS Funnel route; never enroll or reset this node.
set -euo pipefail
[[ $# -le 1 ]] || { echo "usage: bash tunnel-funnel.sh [start|off]" >&2; exit 2; }
exec python3 "$(dirname "$0")/funnel.py" "${1:-start}"
