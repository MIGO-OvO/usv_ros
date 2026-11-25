#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_env.sh"

lock_runtime "$@"
bash "$SCRIPT_DIR/stop_usv_all.sh"
exec bash "$SCRIPT_DIR/start_usv_all.sh" "$@"

