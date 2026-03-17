#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_env.sh"

ensure_run_dirs

MASTER_PID_FILE="$RUN_DIR/roscore.pid"
LAUNCH_PID_FILE="$RUN_DIR/usv_system.pid"
MASTER_LOG_FILE="$LOG_DIR/roscore.log"
LAUNCH_LOG_FILE="$LOG_DIR/usv_system.log"
HOTSPOT_IFACE="${HOTSPOT_IFACE:-wlan0}"
HOTSPOT_CONN_NAME="${HOTSPOT_CONN_NAME:-USV_AP}"
HOTSPOT_IP="${HOTSPOT_IP:-10.42.0.1}"
WEB_PORT="${WEB_PORT:-5000}"

print_status() {
    local name="$1"
    local pid_file="$2"
    local log_file="$3"

    if [[ -f "$pid_file" ]]; then
        local pid
        pid="$(cat "$pid_file")"
        if is_pid_running "$pid"; then
            echo "$name: RUNNING (pid=$pid)"
            echo "  log=$log_file"
            return
        fi
        echo "$name: STOPPED (stale pid file: $pid_file)"
        return
    fi

    echo "$name: STOPPED"
}

print_hotspot_status() {
    local iface_state="missing"
    local ip_state="missing"
    local conn_state="unknown"
    local port_state="closed"

    if command -v ip >/dev/null 2>&1 && ip link show "$HOTSPOT_IFACE" >/dev/null 2>&1; then
        iface_state="present"
        if ip -4 addr show "$HOTSPOT_IFACE" | grep -Fq "$HOTSPOT_IP/"; then
            ip_state="assigned"
        fi
    fi

    if command -v nmcli >/dev/null 2>&1; then
        if nmcli -t -f NAME con show 2>/dev/null | grep -Fxq "$HOTSPOT_CONN_NAME"; then
            conn_state="configured"
        else
            conn_state="missing"
        fi

        if nmcli -t -f NAME con show --active 2>/dev/null | grep -Fxq "$HOTSPOT_CONN_NAME"; then
            conn_state="active"
        fi
    else
        conn_state="nmcli-missing"
    fi

    if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -Eq "[.:]${WEB_PORT}[[:space:]]"; then
        port_state="listening"
    fi

    echo "hotspot: iface=$HOTSPOT_IFACE state=$iface_state conn=$conn_state ip=$ip_state web_port=$port_state"
    echo "  target_ip=$HOTSPOT_IP"

    if [[ "$iface_state" == "present" && "$ip_state" == "assigned" && "$conn_state" == "active" && "$port_state" == "listening" ]]; then
        echo "  web=http://$HOTSPOT_IP:$WEB_PORT"
    else
        local issues=()
        [[ "$iface_state" == "present" ]] || issues+=("interface-not-found")
        [[ "$ip_state" == "assigned" ]] || issues+=("ip-not-assigned")
        [[ "$conn_state" == "active" ]] || issues+=("connection-not-active")
        [[ "$port_state" == "listening" ]] || issues+=("web-port-not-listening")
        echo "  issues=${issues[*]}"
    fi
}

echo "USV Runtime Status"
echo "run_dir=$RUN_DIR"
print_status "roscore" "$MASTER_PID_FILE" "$MASTER_LOG_FILE"
print_status "usv_system" "$LAUNCH_PID_FILE" "$LAUNCH_LOG_FILE"
print_hotspot_status

