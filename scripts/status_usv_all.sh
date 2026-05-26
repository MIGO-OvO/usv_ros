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
HOTSPOT_BAND="${HOTSPOT_BAND:-5g}"
HOTSPOT_CHANNEL="${HOTSPOT_CHANNEL:-149}"
WEB_PORT="${WEB_PORT:-5000}"
USV_SSH_USER="${USV_SSH_USER:-jetson}"

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

hotspot_band_label() {
    case "$1" in
        a)
            echo "5g"
            ;;
        bg)
            echo "2.4g"
            ;;
        "")
            echo "unknown"
            ;;
        *)
            echo "$1"
            ;;
    esac
}

get_default_iface() {
    if ! command -v ip >/dev/null 2>&1; then
        return 0
    fi

    ip route show default 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i=="dev") {print $(i+1); exit}}' | head -n 1
}

is_management_iface() {
    local iface="$1"

    case "$iface" in
        ""|lo)
            return 1
            ;;
        l4tbr*|docker*|br-*|virbr*|veth*)
            return 1
            ;;
        *)
            return 0
            ;;
    esac
}

get_iface_ipv4() {
    local iface="$1"
    if [[ -z "$iface" ]] || ! command -v ip >/dev/null 2>&1; then
        return 0
    fi

    ip -o -4 addr show dev "$iface" scope global 2>/dev/null | awk '{split($4, a, "/"); print a[1]; exit}'
}

get_first_management_candidate() {
    if ! command -v ip >/dev/null 2>&1; then
        return 0
    fi

    local line iface cidr ip_addr
    while read -r line; do
        iface="$(echo "$line" | awk '{print $2}')"
        cidr="$(echo "$line" | awk '{print $4}')"
        if ! is_management_iface "$iface"; then
            continue
        fi
        ip_addr="${cidr%%/*}"
        if [[ "$ip_addr" == "$HOTSPOT_IP" ]]; then
            continue
        fi
        if [[ -n "$ip_addr" ]]; then
            echo "$iface $ip_addr"
            return 0
        fi
    done < <(ip -o -4 addr show scope global 2>/dev/null)
}

print_hotspot_status() {
    local iface_state="missing"
    local ip_state="missing"
    local conn_state="unknown"
    local port_state="closed"
    local hotspot_band="$HOTSPOT_BAND"
    local hotspot_channel="$HOTSPOT_CHANNEL"

    if command -v ip >/dev/null 2>&1 && ip link show "$HOTSPOT_IFACE" >/dev/null 2>&1; then
        iface_state="present"
        if ip -4 addr show "$HOTSPOT_IFACE" | grep -Fq "$HOTSPOT_IP/"; then
            ip_state="assigned"
        fi
    fi

    if command -v nmcli >/dev/null 2>&1; then
        if nmcli -t -f NAME con show 2>/dev/null | grep -Fxq "$HOTSPOT_CONN_NAME"; then
            conn_state="configured"
            local nm_band nm_channel
            nm_band="$(nmcli -g 802-11-wireless.band con show "$HOTSPOT_CONN_NAME" 2>/dev/null || true)"
            nm_channel="$(nmcli -g 802-11-wireless.channel con show "$HOTSPOT_CONN_NAME" 2>/dev/null || true)"
            if [[ -n "$nm_band" ]]; then
                hotspot_band="$(hotspot_band_label "$nm_band")"
            fi
            if [[ -n "$nm_channel" && "$nm_channel" != "0" ]]; then
                hotspot_channel="$nm_channel"
            fi
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

    echo "hotspot: iface=$HOTSPOT_IFACE state=$iface_state conn=$conn_state ip=$ip_state band=$hotspot_band channel=$hotspot_channel web_port=$port_state"
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

print_access_addresses() {
    local default_iface
    local management_iface=""
    local lan_ip=""
    local candidate=""
    local hotspot_web="unavailable"

    default_iface="$(get_default_iface)"
    if is_management_iface "$default_iface"; then
        management_iface="$default_iface"
        lan_ip="$(get_iface_ipv4 "$management_iface")"
        if [[ "$lan_ip" == "$HOTSPOT_IP" ]]; then
            management_iface=""
            lan_ip=""
        fi
    fi

    if [[ -z "$lan_ip" ]]; then
        candidate="$(get_first_management_candidate)"
        if [[ -n "$candidate" ]]; then
            management_iface="${candidate%% *}"
            lan_ip="${candidate#* }"
        fi
    fi

    if command -v ip >/dev/null 2>&1 && ip -4 addr show "$HOTSPOT_IFACE" 2>/dev/null | grep -Fq "$HOTSPOT_IP/"; then
        hotspot_web="http://$HOTSPOT_IP:$WEB_PORT"
    fi

    echo "USV Access Addresses"
    if [[ -n "$lan_ip" ]]; then
        echo "lan_ip: $lan_ip iface=$management_iface"
        echo "ssh: ssh $USV_SSH_USER@$lan_ip"
        echo "web_tunnel: ssh -N -L $WEB_PORT:127.0.0.1:$WEB_PORT $USV_SSH_USER@$lan_ip"
    else
        echo "lan_ip: unavailable"
        echo "ssh: unavailable (no non-hotspot IPv4 address found)"
        echo "web_tunnel: unavailable (no non-hotspot IPv4 address found)"
    fi
    echo "web_local: http://127.0.0.1:$WEB_PORT"
    echo "hotspot_web: $hotspot_web"
    echo "hostname_hint: ssh $USV_SSH_USER@$(hostname).local"
}

print_internet_status() {
    local route_state="missing"
    local default_iface="none"
    local route_source="none"
    local dns_state="unknown"
    local github_state="unknown"
    local default_route=""

    if command -v ip >/dev/null 2>&1; then
        default_route="$(ip route show default 2>/dev/null | head -n 1 || true)"
        if [[ -n "$default_route" ]]; then
            route_state="present"
            default_iface="$(echo "$default_route" | awk '{for (i=1; i<=NF; i++) if ($i=="dev") {print $(i+1); exit}}')"
            if [[ -z "$default_iface" ]]; then
                default_iface="unknown"
            fi
            if [[ "$default_iface" == "$HOTSPOT_IFACE" ]]; then
                route_source="hotspot"
            else
                route_source="external"
            fi
        fi
    else
        route_state="ip-missing"
    fi

    if command -v getent >/dev/null 2>&1; then
        if getent hosts github.com >/dev/null 2>&1; then
            dns_state="ok"
        else
            dns_state="failed"
        fi
    else
        dns_state="getent-missing"
    fi

    if command -v curl >/dev/null 2>&1; then
        if [[ "$dns_state" == "ok" ]] && curl -fsS --connect-timeout 3 --max-time 5 https://github.com/ >/dev/null 2>&1; then
            github_state="reachable"
        elif [[ "$dns_state" != "ok" ]]; then
            github_state="skipped-dns"
        else
            github_state="unreachable"
        fi
    else
        github_state="curl-missing"
    fi

    echo "internet: route=$route_state iface=$default_iface source=$route_source dns=$dns_state github=$github_state"

    if [[ "$route_source" == "hotspot" ]]; then
        echo "  issue=default-route-uses-hotspot-interface"
    elif [[ "$route_state" != "present" ]]; then
        echo "  issue=default-route-missing"
    fi
}

print_full_status() {
    echo "USV Runtime Status"
    echo "run_dir=$RUN_DIR"
    print_status "roscore" "$MASTER_PID_FILE" "$MASTER_LOG_FILE"
    print_status "mavlink_router" "$ROUTER_PID_FILE" "$ROUTER_LOG_FILE"
    print_status "usv_system" "$LAUNCH_PID_FILE" "$LAUNCH_LOG_FILE"
    print_hotspot_status
    print_internet_status

    # 关闭 errexit 以防止 ROS 检查失败中断脚本
    set +e
    print_ros_nodes
    set -e
}

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

case "${1:-full}" in
    full|status)
        print_full_status
        ;;
    hotspot|ap)
        print_hotspot_status
        ;;
    addr|address)
        print_access_addresses
        ;;
    *)
        echo "ERROR: unknown status view: $1" >&2
        echo "Usage: status_usv_all.sh [full|hotspot|addr]" >&2
        exit 2
        ;;
esac
