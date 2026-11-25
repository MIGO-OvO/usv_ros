#!/usr/bin/env bash
# USV Wi-Fi Hotspot Setup Script
# 在 Jetson Nano 上创建 Wi-Fi 热点。
#
# 使用方法:
#   sudo ./setup_hotspot.sh [SSID] [PASSWORD]
#
# 默认:
#   SSID: USV_Control
#   PASSWORD: 12345678
#
# 说明:
#   - 当前 Jetson / NetworkManager 环境下，开放热点配置存在兼容性问题。
#   - 默认改为创建 WPA-PSK 热点，优先保证现场可用性。

set -euo pipefail

SSID="${1:-USV_Control}"
PASSWORD="${2:-12345678}"
INTERFACE="${HOTSPOT_IFACE:-wlan1}"
INTERNET_IFACE="${INTERNET_IFACE:-wlan0}"
INTERNET_BAND="${INTERNET_BAND:-2.4g}"
INTERNET_WIFI_RECONNECT="${INTERNET_WIFI_RECONNECT:-false}"
CON_NAME="${HOTSPOT_CONN_NAME:-USV_AP}"
HOTSPOT_IP="${HOTSPOT_IP:-10.42.0.1}"
HOTSPOT_ROUTE_METRIC="${HOTSPOT_ROUTE_METRIC:-900}"
HOTSPOT_BAND="${HOTSPOT_BAND:-5g}"
HOTSPOT_ALLOW_INTERNET_IFACE="${HOTSPOT_ALLOW_INTERNET_IFACE:-false}"
WS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
RUN_DIR="$WS_DIR/.usv_run"
PREV_WIFI_FILE="$RUN_DIR/previous_wifi_connection"

case "${HOTSPOT_BAND,,}" in
    5g|5ghz|a)
        NM_HOTSPOT_BAND="a"
        HOTSPOT_BAND_LABEL="5g"
        DEFAULT_HOTSPOT_CHANNEL="149"
        ;;
    2.4g|2g|24g|2.4ghz|bg|b/g|g)
        NM_HOTSPOT_BAND="bg"
        HOTSPOT_BAND_LABEL="2.4g"
        DEFAULT_HOTSPOT_CHANNEL="6"
        ;;
    *)
        echo "错误: HOTSPOT_BAND 只支持 5g 或 2.4g，当前为: $HOTSPOT_BAND"
        exit 1
        ;;
esac

HOTSPOT_CHANNEL="${HOTSPOT_CHANNEL:-$DEFAULT_HOTSPOT_CHANNEL}"

mkdir -p "$RUN_DIR"

get_default_route_iface() {
    ip route show default 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i=="dev") {print $(i+1); exit}}' | head -n 1
}

normalize_nm_wifi_band() {
    case "${1,,}" in
        2.4g|2g|24g|2.4ghz|bg|b/g|g)
            echo "bg"
            ;;
        5g|5ghz|a)
            echo "a"
            ;;
        *)
            echo "错误: INTERNET_BAND 只支持 2.4g 或 5g，当前为: $1"
            return 1
            ;;
    esac
}

get_active_wifi_connection_for_iface() {
    local iface="$1"
    nmcli -t -f NAME,DEVICE,TYPE con show --active 2>/dev/null | awk -F: -v iface="$iface" '$2==iface && $3=="802-11-wireless" {print $1; exit}'
}

configure_internet_wifi_band() {
    local internet_nm_band active_conn

    if [[ -z "$INTERNET_IFACE" ]]; then
        echo "跳过外网 Wi-Fi 频段配置: INTERNET_IFACE 为空"
        return 0
    fi
    if ! ip link show "$INTERNET_IFACE" >/dev/null 2>&1; then
        echo "跳过外网 Wi-Fi 频段配置: 接口不存在 ($INTERNET_IFACE)"
        return 0
    fi

    internet_nm_band="$(normalize_nm_wifi_band "$INTERNET_BAND")"
    active_conn="$(get_active_wifi_connection_for_iface "$INTERNET_IFACE")"
    if [[ -z "$active_conn" ]]; then
        echo "跳过外网 Wi-Fi 频段配置: $INTERNET_IFACE 上无活动 Wi-Fi 连接"
        return 0
    fi

    nmcli connection modify "$active_conn" 802-11-wireless.band "$internet_nm_band"
    echo "外网 Wi-Fi profile 已设为 $INTERNET_BAND: iface=$INTERNET_IFACE conn=$active_conn"

    if [[ "$INTERNET_WIFI_RECONNECT" == "true" ]]; then
        echo "立即重连外网 Wi-Fi: $active_conn"
        nmcli connection down "$active_conn" >/dev/null 2>&1 || true
        nmcli connection up "$active_conn" >/dev/null
    else
        echo "外网 Wi-Fi 暂不重连；如需立即应用频段，设置 INTERNET_WIFI_RECONNECT=true"
    fi
}

echo "=========================================="
echo "USV Wi-Fi Hotspot Setup"
echo "=========================================="
echo "SSID: $SSID"
echo "Password: $PASSWORD"
echo "Security: WPA-PSK"
echo "Interface: $INTERFACE"
echo "Internet interface: $INTERNET_IFACE"
echo "Internet band target: $INTERNET_BAND"
echo "Band: $HOTSPOT_BAND_LABEL"
echo "Channel: $HOTSPOT_CHANNEL"
echo "Route metric: $HOTSPOT_ROUTE_METRIC"
echo ""

if [ "$EUID" -ne 0 ]; then
    echo "请使用 sudo 运行此脚本"
    exit 1
fi

if ! command -v nmcli >/dev/null 2>&1; then
    echo "错误: nmcli 未安装"
    exit 1
fi

if ! ip link show "$INTERFACE" >/dev/null 2>&1; then
    echo "错误: 接口 $INTERFACE 不存在"
    exit 1
fi

if [ "${#PASSWORD}" -lt 8 ]; then
    echo "错误: WPA-PSK 密码长度必须 >= 8"
    exit 1
fi

DEFAULT_ROUTE_IFACE="$(get_default_route_iface)"
if [[ "$INTERFACE" == "$INTERNET_IFACE" ]]; then
    echo "警告: HOTSPOT_IFACE 与 INTERNET_IFACE 都是 $INTERFACE，双网卡部署建议外网用 wlan0、热点用 wlan1"
fi

if [[ -n "$DEFAULT_ROUTE_IFACE" && "$DEFAULT_ROUTE_IFACE" == "$INTERFACE" && "$HOTSPOT_ALLOW_INTERNET_IFACE" != "true" ]]; then
    echo "错误: default-route-uses-hotspot-interface"
    echo "当前默认路由正在使用 $INTERFACE。为避免断开外网，不会把该接口切换为热点。"
    echo "建议: INTERNET_IFACE=wlan0 HOTSPOT_IFACE=wlan1；如现场确认允许复用同一网卡，可设置 HOTSPOT_ALLOW_INTERNET_IFACE=true。"
    exit 1
fi

configure_internet_wifi_band

PREVIOUS_WIFI="$(nmcli -t -f NAME,DEVICE,TYPE con show --active 2>/dev/null | awk -F: -v iface="$INTERFACE" '$2==iface && $3=="802-11-wireless" {print $1; exit}')"
if [ -n "$PREVIOUS_WIFI" ] && [ "$PREVIOUS_WIFI" != "$CON_NAME" ]; then
    echo "$PREVIOUS_WIFI" > "$PREV_WIFI_FILE"
    echo "记录上一个 WiFi 连接: $PREVIOUS_WIFI"
else
    rm -f "$PREV_WIFI_FILE"
    echo "未检测到可回连的上一个 WiFi 连接"
fi

echo "清理旧连接..."
nmcli con delete "$CON_NAME" >/dev/null 2>&1 || true

echo "断开 $INTERFACE 当前连接..."
nmcli dev disconnect "$INTERFACE" >/dev/null 2>&1 || true

echo "创建热点连接..."
nmcli connection add type wifi ifname "$INTERFACE" con-name "$CON_NAME" ssid "$SSID" >/dev/null
nmcli connection modify "$CON_NAME" \
    802-11-wireless.mode ap \
    802-11-wireless.band "$NM_HOTSPOT_BAND" \
    802-11-wireless.channel "$HOTSPOT_CHANNEL" \
    ipv4.method shared \
    ipv4.never-default yes \
    ipv4.route-metric "$HOTSPOT_ROUTE_METRIC" \
    ipv6.method ignore \
    ipv6.never-default yes \
    ipv6.route-metric "$HOTSPOT_ROUTE_METRIC" \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$PASSWORD" \
    connection.autoconnect yes >/dev/null

echo "启动热点连接..."
if nmcli connection up "$CON_NAME" >/dev/null 2>&1; then
    echo ""
    echo "=========================================="
    echo "热点创建成功!"
    echo "=========================================="
    echo "SSID: $SSID"
    echo "Password: $PASSWORD"
    echo "Security: WPA-PSK"
    echo "Band: $HOTSPOT_BAND_LABEL"
    echo "Channel: $HOTSPOT_CHANNEL"
    echo "热点 IP: $HOTSPOT_IP"
    echo ""
    echo "当前 $INTERFACE 状态:"
    ip -4 addr show "$INTERFACE" | grep "inet " || echo "  未分配 IP"
    echo ""
    echo "Web 配置界面:"
    echo "  - 通过热点: http://$HOTSPOT_IP:5000"
    echo ""
    echo "连接详情:"
    nmcli -f GENERAL.NAME,GENERAL.DEVICES,802-11-wireless.ssid,802-11-wireless.mode,802-11-wireless.band,802-11-wireless.channel,802-11-wireless-security.key-mgmt con show "$CON_NAME"
    echo ""
else
    echo "热点创建失败!"
    echo ""
    echo "可能原因:"
    echo "1. $INTERFACE 已被其他连接占用"
    echo "2. 网络接口不支持 AP 模式"
    echo "3. NetworkManager 不允许当前无线芯片创建热点"
    echo ""
    echo "建议排查:"
    echo "  nmcli dev status"
    echo "  nmcli -p connection show '$CON_NAME'"
    echo "  nmcli dev wifi list ifname $INTERFACE"
    if [ "$NM_HOTSPOT_BAND" = "a" ]; then
        echo ""
        echo "5GHz AP 可能受地区码、驱动或网卡限制。可显式改用 2.4GHz 固定信道重试:"
        echo "  sudo HOTSPOT_BAND=2.4g HOTSPOT_CHANNEL=6 HOTSPOT_IFACE=$INTERFACE ./src/usv_ros/scripts/setup_hotspot.sh $SSID $PASSWORD"
    fi
    exit 1
fi

echo "当前网络状态:"
nmcli dev status
