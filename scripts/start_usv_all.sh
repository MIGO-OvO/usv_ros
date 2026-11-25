#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_env.sh"

lock_runtime "$@"
load_ros_env
print_workspace_info

MASTER_PID_FILE="$RUN_DIR/roscore.pid"
MASTER_LOG_FILE="$LOG_DIR/roscore.log"
LAUNCH_PID_FILE="$RUN_DIR/usv_system.pid"
LAUNCH_LOG_FILE="$LOG_DIR/usv_system.log"
WEB_PORT="${WEB_PORT:-5000}"
web_host=127.0.0.1
enable_web=true
for arg in "$@"; do
    case "$arg" in
        web_port:=*) WEB_PORT="${arg#web_port:=}" ;;
        web_host:=*) web_host="${arg#web_host:=}" ;;
        enable_web:=*) enable_web="${arg#enable_web:=}" ;;
    esac
done
case "$web_host" in
    0.0.0.0) web_host=127.0.0.1 ;;
    ::) web_host='[::1]' ;;
    *:*) web_host="[$web_host]" ;;
esac

if [[ -f "$LAUNCH_PID_FILE" ]]; then
    launch_pid="$(cat "$LAUNCH_PID_FILE")"
    if is_pid_running "$launch_pid" && is_pid_owned_by "$launch_pid" "roslaunch"; then
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

stop_orphaned_web
if [[ "${enable_web,,}" != false && "$enable_web" != 0 ]]; then
    require_free_tcp_port "$WEB_PORT"
fi

ros_master_ready() {
    timeout 3 rostopic list >/dev/null 2>&1
}

if ! ros_master_ready; then
    log "未检测到 roscore，开始后台启动 ROS Master"
    start_background_process "$MASTER_PID_FILE" "$MASTER_LOG_FILE" roscore
    if ! wait_until 15 ros_master_ready; then
        log "ROS Master 未就绪，查看 $MASTER_LOG_FILE"
        exit 1
    fi
fi

start_mavlink_router

log "ROS Master 与 mavlink-router 已就绪，开始后台启动 usv_ros 系统"
start_background_process "$LAUNCH_PID_FILE" "$LAUNCH_LOG_FILE" \
    roslaunch usv_ros usv_bringup.launch "web_port:=$WEB_PORT" \
    "mavlink_router_url:=tcp:127.0.0.1:$ROUTER_TCP_PORT" "$@"
launch_pid="$(<"$LAUNCH_PID_FILE")"

web_ready() {
    listener_belongs_to "$WEB_PORT" "$launch_pid" &&
        curl --noproxy '*' -fsS --max-time 2 "http://$web_host:$WEB_PORT/api/ui/debug" >/dev/null 2>&1 &&
        timeout 3 rosnode ping -c 1 /web_config_server >/dev/null 2>&1
}

if [[ "${enable_web,,}" != false && "$enable_web" != 0 ]]; then
    if ! wait_until "${USV_BOOT_WAIT_SECONDS:-90}" web_ready; then
        log "本轮 Web 服务未在 TCP $WEB_PORT 就绪，查看 $LAUNCH_LOG_FILE"
        exit 1
    fi
fi
if ! is_pid_running "$launch_pid"; then
    log "roslaunch 已退出，查看 $LAUNCH_LOG_FILE"
    exit 1
fi
log "一键启动完成"
log "查看 ROS Master 日志: tail -f $MASTER_LOG_FILE"
log "查看路由日志: tail -f $ROUTER_LOG_FILE"
log "查看系统日志: tail -f $LAUNCH_LOG_FILE"
