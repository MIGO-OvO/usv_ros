#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_env.sh"

ensure_run_dirs
print_workspace_info

MASTER_PID_FILE="$RUN_DIR/roscore.pid"
LAUNCH_PID_FILE="$RUN_DIR/usv_system.pid"
WEB_PORT="${WEB_PORT:-5000}"

stop_pid_file "$LAUNCH_PID_FILE" "usv system"
stop_pid_file "$ROUTER_PID_FILE" "mavlink-router"
stop_pid_file "$MASTER_PID_FILE" "roscore"
cleanup_port_process "$WEB_PORT"

log "一键停止完成"

