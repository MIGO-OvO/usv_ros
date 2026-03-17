#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$SCRIPT_DIR/stop_usv_all.sh"
sleep 2
"$SCRIPT_DIR/start_usv_all.sh" "$@"

