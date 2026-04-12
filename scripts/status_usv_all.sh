#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_env.sh"

ensure_run_dirs

MASTER_PID_FILE="$RUN_DIR/roscore.pid"
LAUNCH_PID_FILE="$RUN_DIR/usv_system.pid"
ROUTER_PID_FILE="$RUN_DIR/mavlink_router.pid"
MASTER_LOG_FILE="$LOG_DIR/roscore.log"
LAUNCH_LOG_FILE="$LOG_DIR/usv_system.log"
ROUTER_LOG_FILE="$LOG_DIR/mavlink_router.log"
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
print_status "mavlink_router" "$ROUTER_PID_FILE" "$ROUTER_LOG_FILE"
print_status "usv_system" "$LAUNCH_PID_FILE" "$LAUNCH_LOG_FILE"
print_hotspot_status

# ── ROS 节点级健康检查 ──────────────────────────────────────────────
# 仅在 roscore 运行时执行；使用 timeout 避免 hang；失败不中断脚本
EXPECTED_NODES="/pump_control_node /web_config_server /mavlink_trigger_node /usv_mavlink_bridge /mavros"
ROS_CHECK_TIMEOUT=3   # rosnode/mavros 检查超时秒数
DIAG_CHECK_TIMEOUT=12 # bridge 诊断发布间隔为 10s，等 12s 确保能收到

print_ros_nodes() {
    # 尝试加载 ROS 环境
    if [[ -f "$ROS_SETUP" ]]; then
        set +u
        # shellcheck disable=SC1090
        source "$ROS_SETUP"
        if [[ -f "$WORKSPACE_SETUP" ]]; then
            # shellcheck disable=SC1090
            source "$WORKSPACE_SETUP"
        fi
        set -u
    fi

    if ! command -v rosnode >/dev/null 2>&1; then
        echo "ros_nodes: SKIP (rosnode 命令不可用)"
        return
    fi

    # 检查 roscore 是否可达
    if ! timeout "$ROS_CHECK_TIMEOUT" rostopic list >/dev/null 2>&1; then
        echo "ros_nodes: SKIP (roscore 不可达)"
        return
    fi

    local node_list
    node_list="$(timeout "$ROS_CHECK_TIMEOUT" rosnode list 2>/dev/null || true)"
    if [[ -z "$node_list" ]]; then
        echo "ros_nodes: EMPTY (roscore 可达但无节点注册)"
        return
    fi

    local missing=()
    local running=()
    for node in $EXPECTED_NODES; do
        if echo "$node_list" | grep -Fxq "$node"; then
            running+=("$node")
        else
            missing+=("$node")
        fi
    done

    if [[ ${#missing[@]} -eq 0 ]]; then
        echo "ros_nodes: ALL_OK (${#running[@]}/${#running[@]})"
    else
        echo "ros_nodes: DEGRADED (running=${#running[@]} missing=${#missing[@]})"
        for m in "${missing[@]}"; do
            echo "  MISSING: $m"
        done
    fi

    # MAVROS 连通性
    local mavros_state
    mavros_state="$(timeout "$ROS_CHECK_TIMEOUT" rostopic echo -n 1 /mavros/state 2>/dev/null || true)"
    if [[ -z "$mavros_state" ]]; then
        echo "mavros_link: UNKNOWN (超时未响应)"
    elif echo "$mavros_state" | grep -q "connected: True"; then
        echo "mavros_link: CONNECTED"
    else
        echo "mavros_link: DISCONNECTED (飞控未连通)"
    fi

    # Bridge 诊断摘要
    local bridge_diag
    bridge_diag="$(timeout "$DIAG_CHECK_TIMEOUT" rostopic echo -n 1 /usv/bridge_diagnostics 2>/dev/null || true)"
    if [[ -z "$bridge_diag" ]]; then
        echo "bridge_diag: UNKNOWN (超时未响应)"
    else
        # 兼容 rostopic echo 的多种输出：
        # 1) 纯 JSON
        # 2) data: "{...}" 包装
        # 3) Python dict 单引号格式
        echo "$bridge_diag" | python3 -c "
import sys, json, re, ast
text = sys.stdin.read().strip()
obj = None
candidates = []

# 纯 {...}
for m in re.finditer(r'\{.*?\}', text):
    candidates.append(m.group())

# data: '...'/"..." 包装
for m in re.finditer(r'data:\s*[\"\'](.*)[\"\']', text):
    candidates.append(m.group(1))

# 整体文本也尝试一次
candidates.append(text)

for raw in candidates:
    raw = raw.strip()
    if not raw:
        continue
    try:
        obj = json.loads(raw)
        break
    except Exception:
        pass
    try:
        parsed = ast.literal_eval(raw)
        if isinstance(parsed, dict):
            obj = parsed
            break
        if isinstance(parsed, str):
            try:
                obj = json.loads(parsed)
                break
            except Exception:
                parsed2 = ast.literal_eval(parsed)
                if isinstance(parsed2, dict):
                    obj = parsed2
                    break
    except Exception:
        pass

if not isinstance(obj, dict):
    print('bridge_diag: PARSE_ERROR (no structured payload found)')
else:
    print('bridge_diag: mavros={} tx={} pkt={} drops={}'.format(
        obj.get('mavros_connected', '?'),
        obj.get('tx_total', '?'),
        obj.get('pkt_count', '?'),
        obj.get('mavros_drops', '?')))
" 2>/dev/null || echo "bridge_diag: PARSE_ERROR"
    fi
}

# 关闭 errexit 以防止 ROS 检查失败中断脚本
set +e
print_ros_nodes
set -e

