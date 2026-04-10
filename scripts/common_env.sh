#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKG_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
WS_DIR="$(cd "$PKG_DIR/../.." && pwd)"
ROS_SETUP="/opt/ros/noetic/setup.bash"
WORKSPACE_SETUP="$WS_DIR/devel/setup.bash"
RUN_DIR="$WS_DIR/.usv_run"
LOG_DIR="$RUN_DIR/logs"
ROUTER_PID_FILE="$RUN_DIR/mavlink_router.pid"
ROUTER_LOG_FILE="$LOG_DIR/mavlink_router.log"
MAVLINK_ROUTERD_BIN="${MAVLINK_ROUTERD_BIN:-mavlink-routerd}"
FCU_UART_DEVICE="${FCU_UART_DEVICE:-/dev/ttyTHS1}"
FCU_UART_BAUD="${FCU_UART_BAUD:-921600}"
ROUTER_MAVROS_UDP="${ROUTER_MAVROS_UDP:-127.0.0.1:14550}"
ROUTER_BRIDGE_UDP="${ROUTER_BRIDGE_UDP:-127.0.0.1:14551}"
ROUTER_TCP_PORT="${ROUTER_TCP_PORT:-5760}"

start_mavlink_router() {
    ensure_run_dirs
    if [[ -f "$ROUTER_PID_FILE" ]]; then
        local old_pid
        old_pid="$(cat "$ROUTER_PID_FILE")"
        if is_pid_running "$old_pid"; then
            log "mavlink-router 已在运行 (pid=$old_pid)"
            return 0
        fi
        rm -f "$ROUTER_PID_FILE"
    fi
    if ! command -v "$MAVLINK_ROUTERD_BIN" >/dev/null 2>&1; then
        log "缺少 mavlink-routerd，请先安装或设置 MAVLINK_ROUTERD_BIN"
        exit 1
    fi
    cleanup_port_process "$ROUTER_TCP_PORT"
    log "启动 mavlink-router: uart=$FCU_UART_DEVICE:$FCU_UART_BAUD tcp=$ROUTER_TCP_PORT udp=$ROUTER_MAVROS_UDP,$ROUTER_BRIDGE_UDP"
    start_background_process "$ROUTER_PID_FILE" "$ROUTER_LOG_FILE" "$MAVLINK_ROUTERD_BIN" -e "$ROUTER_MAVROS_UDP" -e "$ROUTER_BRIDGE_UDP" "$FCU_UART_DEVICE:$FCU_UART_BAUD"
}


log() {
    echo "[usv-startup] $*"
}

ensure_file() {
    local file_path="$1"
    local hint="$2"
    if [[ ! -f "$file_path" ]]; then
        log "缺少文件: $file_path"
        log "$hint"
        exit 1
    fi
}

ensure_run_dirs() {
    mkdir -p "$RUN_DIR" "$LOG_DIR"
}

load_ros_env() {
    ensure_file "$ROS_SETUP" "请确认 ROS Noetic 已安装。"
    # 临时关闭未绑定变量检查，防止 ROS setup 脚本报错
    set +u
    # shellcheck disable=SC1090
    source "$ROS_SETUP"

    ensure_file "$WORKSPACE_SETUP" "请先在工作空间根目录执行 catkin_make。"
    # shellcheck disable=SC1090
    source "$WORKSPACE_SETUP"
    set -u
}

require_roscore() {
    if ! command -v rostopic >/dev/null 2>&1; then
        log "ROS 环境未加载，无法执行 roscore 检查。"
        exit 1
    fi

    if ! rostopic list >/dev/null 2>&1; then
        log "未检测到正在运行的 roscore。"
        log "请先执行: $SCRIPT_DIR/start_ros_master.sh"
        exit 1
    fi
}

print_workspace_info() {
    log "workspace=$WS_DIR"
    log "package=$PKG_DIR"
}

is_pid_running() {
    local pid="$1"
    [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1
}

stop_pid_file() {
    local pid_file="$1"
    local process_name="$2"

    if [[ ! -f "$pid_file" ]]; then
        return 0
    fi

    local pid
    pid="$(cat "$pid_file")"
    if is_pid_running "$pid"; then
        log "停止 $process_name (pid=$pid)"
        kill "$pid"
        for _ in {1..20}; do
            if ! is_pid_running "$pid"; then
                break
            fi
            sleep 0.5
        done
        if is_pid_running "$pid"; then
            log "$process_name 未在超时内退出，执行强制停止"
            kill -9 "$pid"
        fi
    fi

    rm -f "$pid_file"
}

cleanup_port_process() {
    local port="$1"

    if command -v lsof >/dev/null 2>&1; then
        local pids
        pids="$(lsof -ti tcp:"$port" 2>/dev/null || true)"
        if [[ -n "$pids" ]]; then
            log "清理占用端口 $port 的旧进程: $pids"
            kill $pids >/dev/null 2>&1 || true
            sleep 1
            for pid in $pids; do
                if is_pid_running "$pid"; then
                    log "端口 $port 进程未退出，强制停止 pid=$pid"
                    kill -9 "$pid" >/dev/null 2>&1 || true
                fi
            done
        fi
    fi
}

start_background_process() {
    local pid_file="$1"
    local log_file="$2"
    shift 2

    ensure_run_dirs
    if [[ -f "$pid_file" ]]; then
        local old_pid
        old_pid="$(cat "$pid_file")"
        if is_pid_running "$old_pid"; then
            log "检测到已有运行中的进程 pid=$old_pid，请先停止。"
            exit 1
        fi
        rm -f "$pid_file"
    fi

    log "后台启动: $*"
    nohup "$@" >>"$log_file" 2>&1 &
    local bg_pid=$!
    echo "$bg_pid" > "$pid_file"
    log "已启动 pid=$bg_pid, log=$log_file"
}
