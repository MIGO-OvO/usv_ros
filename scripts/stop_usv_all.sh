#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_env.sh"

ensure_run_dirs
print_workspace_info

MASTER_PID_FILE="$RUN_DIR/roscore.pid"
LAUNCH_PID_FILE="$RUN_DIR/usv_system.pid"

stop_pid_file "$LAUNCH_PID_FILE" "usv system"
stop_pid_file "$MASTER_PID_FILE" "roscore"

log "一键停止完成"

