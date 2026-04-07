#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_env.sh"

load_ros_env
ensure_run_dirs
print_workspace_info

MASTER_PID_FILE="$RUN_DIR/roscore.pid"
MASTER_LOG_FILE="$LOG_DIR/roscore.log"
LAUNCH_PID_FILE="$RUN_DIR/usv_system.pid"
LAUNCH_LOG_FILE="$LOG_DIR/usv_system.log"
WEB_PORT="${WEB_PORT:-5000}"

if [[ -f "$LAUNCH_PID_FILE" ]]; then
    launch_pid="$(cat "$LAUNCH_PID_FILE")"
    if is_pid_running "$launch_pid"; then
        log "检测到 usv 系统已在运行 (pid=$launch_pid)，请先执行 stop_usv_all.sh"
        exit 1
    fi
    rm -f "$LAUNCH_PID_FILE"
fi

if [[ -f "$MASTER_PID_FILE" ]]; then
    master_pid="$(cat "$MASTER_PID_FILE")"
    if ! is_pid_running "$master_pid"; then
        rm -f "$MASTER_PID_FILE"
    fi
fi

if ! rostopic list >/dev/null 2>&1; then
    log "未检测到 roscore，开始后台启动 ROS Master"
    start_background_process "$MASTER_PID_FILE" "$MASTER_LOG_FILE" roscore
    sleep 3
fi

cleanup_port_process "$WEB_PORT"
start_mavlink_router

require_roscore
log "ROS Master 与 mavlink-router 已就绪，开始后台启动 usv_ros 系统"
start_background_process "$LAUNCH_PID_FILE" "$LAUNCH_LOG_FILE" roslaunch usv_ros usv_bringup.launch "$@"
sleep 5
log "一键启动完成"
log "查看 ROS Master 日志: tail -f $MASTER_LOG_FILE"
log "查看路由日志: tail -f $ROUTER_LOG_FILE"
log "查看系统日志: tail -f $LAUNCH_LOG_FILE"
