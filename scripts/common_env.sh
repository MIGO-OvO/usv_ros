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

# Site settings shared by systemd and the interactive commands.
if [[ -r /etc/default/usv-ros-router ]]; then
    # shellcheck disable=SC1091
    source /etc/default/usv-ros-router
fi
MAVLINK_ROUTERD_BIN="${MAVLINK_ROUTERD_BIN:-mavlink-routerd}"
FCU_UART_DEVICE="${FCU_UART_DEVICE:-/dev/ttyTHS1}"
FCU_UART_BAUD="${FCU_UART_BAUD:-460800}"
ROUTER_MAVROS_UDP="${ROUTER_MAVROS_UDP:-127.0.0.1:14550}"
ROUTER_BRIDGE_UDP="${ROUTER_BRIDGE_UDP:-127.0.0.1:14551}"
ROUTER_TCP_PORT="${ROUTER_TCP_PORT:-5760}"

start_mavlink_router() {
    ensure_run_dirs
    local router_pid
    if [[ -f "$ROUTER_PID_FILE" ]]; then
        router_pid="$(<"$ROUTER_PID_FILE")"
        if is_pid_running "$router_pid" && is_pid_owned_by "$router_pid" "mavlink-router"; then
            log "mavlink-router 已在运行 (pid=$router_pid)"
            wait_for_router "$router_pid"
            return
        fi
        rm -f "$ROUTER_PID_FILE"
    fi
    if ! command -v "$MAVLINK_ROUTERD_BIN" >/dev/null 2>&1; then
        log "缺少 mavlink-routerd，请先安装或设置 MAVLINK_ROUTERD_BIN"
        exit 1
    fi
    require_free_tcp_port "$ROUTER_TCP_PORT"
    log "启动 mavlink-router: bin=$(command -v "$MAVLINK_ROUTERD_BIN") uart=$FCU_UART_DEVICE:$FCU_UART_BAUD tcp=$ROUTER_TCP_PORT udp=$ROUTER_MAVROS_UDP,$ROUTER_BRIDGE_UDP"
    start_background_process "$ROUTER_PID_FILE" "$ROUTER_LOG_FILE" \
        bash "$SCRIPT_DIR/mavlink-router-supervisor.sh" "$MAVLINK_ROUTERD_BIN" \
        -t "$ROUTER_TCP_PORT" -e "$ROUTER_MAVROS_UDP" -e "$ROUTER_BRIDGE_UDP" "$FCU_UART_DEVICE:$FCU_UART_BAUD"
    wait_for_router "$(<"$ROUTER_PID_FILE")"
}

wait_for_router() {
    if [[ "$ROUTER_TCP_PORT" == 0 ]]; then
        return
    fi
    if ! wait_until 10 listener_belongs_to "$ROUTER_TCP_PORT" "$1"; then
        log "mavlink-router 未在 TCP $ROUTER_TCP_PORT 监听，查看 $ROUTER_LOG_FILE"
        return 1
    fi
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

lock_runtime() {
    if [[ "${USV_RUNTIME_LOCKED:-}" != 1 ]]; then
        ensure_run_dirs
        exec flock --close --timeout 60 "$RUN_DIR/lifecycle.lock" \
            env USV_RUNTIME_LOCKED=1 bash "$0" "$@"
    fi
}

wait_until() {
    local deadline=$((SECONDS + $1))
    shift
    until "$@"; do
        (( SECONDS < deadline )) || return 1
        sleep 1
    done
}

require_free_tcp_port() {
    local port="$1" listeners
    [[ "$port" != 0 ]] || return 0
    listeners="$(ss -H -ltnp "sport = :$port")"
    if [[ -n "$listeners" ]]; then
        log "TCP $port 已被占用"
        printf '%s\n' "$listeners" >&2
        return 1
    fi
}

listener_belongs_to() {
    local port="$1" parent="$2" listeners pid
    listeners="$(ss -H -ltnp "sport = :$port")" || return 1
    [[ -n "$listeners" ]] || return 1
    for pid in "$parent" $(ps -o pid= --ppid "$parent"); do
        [[ "$listeners" == *"pid=$pid,"* ]] && return 0
    done
    return 1
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
    local pid="$1" state
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
    state="$(ps -o stat= -p "$pid")" || return 1
    [[ -n "$state" && "$state" != Z* ]]
}

pid_has_exited() {
    ! is_pid_running "$1"
}

# 校验 PID 对应的进程命令行是否包含预期关键字
# 用法: is_pid_owned_by "12345" "roscore"
is_pid_owned_by() {
    local pid="$1"
    local expect="$2"
    if [[ ! -f "/proc/$pid/cmdline" ]]; then
        return 1
    fi
    tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -qi "$expect"
}

stop_pid_file() {
    local pid_file="$1"
    local process_name="$2"
    local shutdown_timeout="${3:-25}"

    if [[ ! -f "$pid_file" ]]; then
        return 0
    fi

    local pid
    pid="$(cat "$pid_file")"
    if is_pid_running "$pid"; then
        # 安全校验：确认 PID 仍属于目标进程，防止残留 PID 文件误杀无关进程
        if ! is_pid_owned_by "$pid" "$process_name"; then
            log "警告: pid=$pid 非 $process_name 进程，跳过 kill 并清理残留 PID 文件"
            rm -f "$pid_file"
            return 0
        fi
        log "停止 $process_name (pid=$pid)"
        kill -TERM "$pid" 2>/dev/null || pid_has_exited "$pid"
        if ! wait_until "$shutdown_timeout" pid_has_exited "$pid"; then
            log "$process_name 尚未退出 (pid=$pid)，请查看 $LOG_DIR 后重试"
            return 1
        fi
    fi

    rm -f "$pid_file"
}

is_orphaned_web() {
    local pid="$1" arg
    local -a args
    [[ "$(ps -o ppid= -p "$pid" | tr -d ' ')" == 1 ]] || return 1
    mapfile -d '' -t args 2>/dev/null < "/proc/$pid/cmdline" || return 1
    for arg in "${args[@]}"; do
        case "$arg" in
            "$SCRIPT_DIR/web_config_server.py"|"$WS_DIR/devel/lib/usv_ros/web_config_server.py"|"$WS_DIR/install/lib/usv_ros/web_config_server.py")
                [[ " ${args[*]} " == *" __name:=web_config_server "* ]]
                return
                ;;
        esac
    done
    return 1
}

stop_orphaned_web() {
    local pid
    while read -r pid; do
        is_orphaned_web "$pid" || continue
        log "停止旧 roslaunch 遗留的 Web 节点 (pid=$pid)"
        kill -TERM "$pid" 2>/dev/null || pid_has_exited "$pid"
        if ! wait_until 3 pid_has_exited "$pid"; then
            is_orphaned_web "$pid" || continue
            kill -KILL "$pid" 2>/dev/null || pid_has_exited "$pid"
            wait_until 3 pid_has_exited "$pid" || return 1
        fi
    done < <(pgrep -u "$(id -u)" -P 1 -f '[w]eb_config_server\.py' || true)
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
    env -u USV_RUNTIME_LOCKED nohup "$@" </dev/null >>"$log_file" 2>&1 &
    local bg_pid=$!
    echo "$bg_pid" > "$pid_file"
    log "已启动 pid=$bg_pid, log=$log_file"
}
