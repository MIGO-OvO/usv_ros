#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_env.sh"

SERVICE_NAME="${SERVICE_NAME:-usv-boot.service}"
USV_RUN_USER="${USV_RUN_USER:-${SUDO_USER:-$(id -un)}}"
USV_HOTSPOT_SSID="${USV_HOTSPOT_SSID:-USV_Control}"
USV_HOTSPOT_PASSWORD="${USV_HOTSPOT_PASSWORD:-12345678}"
USV_ENABLE_HOTSPOT="${USV_ENABLE_HOTSPOT:-true}"
HOTSPOT_IFACE="${HOTSPOT_IFACE:-wlan0}"
HOTSPOT_IP="${HOTSPOT_IP:-10.42.0.1}"
WEB_PORT="${WEB_PORT:-5000}"
USV_BOOT_WAIT_SECONDS="${USV_BOOT_WAIT_SECONDS:-90}"
USV_STRICT_SELF_CHECK="${USV_STRICT_SELF_CHECK:-true}"
BOOT_CHECK_LOG_FILE="${BOOT_CHECK_LOG_FILE:-$LOG_DIR/boot_check.log}"
STARTED_ROS="false"
HOTSPOT_ATTEMPTED="false"

prepare_boot_log() {
    mkdir -p "$RUN_DIR" "$LOG_DIR"
    touch "$BOOT_CHECK_LOG_FILE"
    if [[ "$(id -u)" -eq 0 && "$USV_RUN_USER" != "root" ]]; then
        chown -R "$USV_RUN_USER:$USV_RUN_USER" "$RUN_DIR" >/dev/null 2>&1 || true
    fi
}

log_boot() {
    echo "[$SERVICE_NAME] $*" | tee -a "$BOOT_CHECK_LOG_FILE"
}

require_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        log_boot "ERROR: run with sudo or systemd root context"
        exit 1
    fi
}

require_command() {
    local cmd="$1"
    if ! command -v "$cmd" >/dev/null 2>&1; then
        log_boot "ERROR: missing command: $cmd"
        exit 1
    fi
}

is_hotspot_enabled() {
    [[ "$USV_ENABLE_HOTSPOT" == "true" ]]
}

run_as_usv_user() {
    local env_args=(
        "WEB_PORT=$WEB_PORT"
        "USV_ENABLE_HOTSPOT=$USV_ENABLE_HOTSPOT"
        "HOTSPOT_IFACE=$HOTSPOT_IFACE"
        "HOTSPOT_IP=$HOTSPOT_IP"
        "HOTSPOT_CONN_NAME=${HOTSPOT_CONN_NAME:-USV_AP}"
        "MAVLINK_ROUTERD_BIN=${MAVLINK_ROUTERD_BIN:-mavlink-routerd}"
        "FCU_UART_DEVICE=${FCU_UART_DEVICE:-/dev/ttyTHS1}"
        "FCU_UART_BAUD=${FCU_UART_BAUD:-921600}"
        "ROUTER_MAVROS_UDP=${ROUTER_MAVROS_UDP:-127.0.0.1:14550}"
        "ROUTER_BRIDGE_UDP=${ROUTER_BRIDGE_UDP:-127.0.0.1:14551}"
        "ROUTER_TCP_PORT=${ROUTER_TCP_PORT:-5760}"
    )

    if [[ "$(id -u)" -eq 0 && "$USV_RUN_USER" != "root" ]]; then
        runuser -u "$USV_RUN_USER" -- env "${env_args[@]}" "$@"
    else
        env "${env_args[@]}" "$@"
    fi
}

wait_for_hotspot() {
    log_boot "wait hotspot ip=$HOTSPOT_IP iface=$HOTSPOT_IFACE"
    for _ in $(seq 1 "$USV_BOOT_WAIT_SECONDS"); do
        if ip -4 addr show "$HOTSPOT_IFACE" 2>/dev/null | grep -Fq "$HOTSPOT_IP/"; then
            log_boot "hotspot ip ready"
            return 0
        fi
        sleep 1
    done
    log_boot "ERROR: hotspot ip not ready"
    return 1
}

wait_for_web() {
    log_boot "wait web port=$WEB_PORT"
    for _ in $(seq 1 "$USV_BOOT_WAIT_SECONDS"); do
        if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -Eq "[.:]${WEB_PORT}[[:space:]]"; then
            log_boot "web port listening"
            return 0
        fi
        if command -v curl >/dev/null 2>&1 && curl -fsS "http://127.0.0.1:${WEB_PORT}/api/ui/debug" >/dev/null 2>&1; then
            log_boot "web api ready"
            return 0
        fi
        sleep 1
    done
    log_boot "ERROR: web port not ready"
    return 1
}

append_status_snapshot() {
    log_boot "status snapshot begin"
    run_as_usv_user "$SCRIPT_DIR/status_usv_all.sh" | tee -a "$BOOT_CHECK_LOG_FILE"
    log_boot "status snapshot end"
}

require_status_line() {
    local pattern="$1"
    local label="$2"
    if ! grep -Fq "$pattern" "$BOOT_CHECK_LOG_FILE"; then
        log_boot "ERROR: self-check missing $label ($pattern)"
        return 1
    fi
}

run_self_check() {
    append_status_snapshot

    require_status_line "roscore: RUNNING" "roscore"
    require_status_line "mavlink_router: RUNNING" "mavlink-router"
    require_status_line "usv_system: RUNNING" "usv roslaunch"
    require_hotspot_self_check
    require_status_line "web_port=listening" "web port"

    if [[ "$USV_STRICT_SELF_CHECK" == "true" ]]; then
        require_status_line "ros_nodes: ALL_OK" "ROS nodes"
        require_status_line "mavros_link: CONNECTED" "MAVROS link"
    else
        log_boot "strict self-check disabled; ROS node and MAVROS diagnostics are informational"
    fi
}

require_hotspot_self_check() {
    if is_hotspot_enabled; then
        require_status_line "conn=active" "hotspot active"
        require_status_line "ip=assigned" "hotspot ip"
    else
        log_boot "hotspot self-check skipped: disabled"
    fi
}

cleanup_on_error() {
    local exit_code=$?
    log_boot "ERROR: boot start failed, cleanup begin (exit=$exit_code)"
    if [[ "$STARTED_ROS" == "true" ]]; then
        run_as_usv_user "$SCRIPT_DIR/stop_usv_all.sh" | tee -a "$BOOT_CHECK_LOG_FILE" || true
    fi
    if [[ "$HOTSPOT_ATTEMPTED" == "true" ]]; then
        "$SCRIPT_DIR/stop_hotspot.sh" | tee -a "$BOOT_CHECK_LOG_FILE" || true
    fi
    log_boot "ERROR: boot start failed, cleanup end"
    exit "$exit_code"
}

main() {
    prepare_boot_log
    : > "$BOOT_CHECK_LOG_FILE"
    trap cleanup_on_error ERR

    log_boot "boot start"
    log_boot "run_user=$USV_RUN_USER ssid=$USV_HOTSPOT_SSID hotspot=$USV_ENABLE_HOTSPOT strict=$USV_STRICT_SELF_CHECK"

    require_root
    if is_hotspot_enabled; then
        require_command nmcli
        require_command ip
    fi
    require_command runuser
    require_command mavlink-routerd

    if is_hotspot_enabled; then
        HOTSPOT_ATTEMPTED="true"
        "$SCRIPT_DIR/setup_hotspot.sh" "$USV_HOTSPOT_SSID" "$USV_HOTSPOT_PASSWORD" | tee -a "$BOOT_CHECK_LOG_FILE"
        wait_for_hotspot
    else
        log_boot "skip hotspot setup: disabled"
    fi

    run_as_usv_user "$SCRIPT_DIR/start_usv_all.sh"
    STARTED_ROS="true"
    wait_for_web
    run_self_check

    log_boot "boot start complete"
    trap - ERR
}

main "$@"
