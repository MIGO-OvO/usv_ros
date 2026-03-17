#!/bin/bash
# USV Wi-Fi Hotspot Setup Script
# 在 Jetson Nano 上创建 Wi-Fi 热点（显式开放网络，无密码）
#
# 使用方法:
#   sudo ./setup_hotspot.sh [SSID]
#
# 默认:
#   SSID: USV_Control
#   安全模式: open

SSID="${1:-USV_Control}"
INTERFACE="wlan0"
CON_NAME="USV_AP"
HOTSPOT_IP="10.42.0.1"

echo "=========================================="
echo "USV Wi-Fi Hotspot Setup"
echo "=========================================="
echo "SSID: $SSID"
echo "安全模式: open (无密码)"
echo "Interface: $INTERFACE"
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

echo "清理旧连接..."
nmcli con delete "$CON_NAME" >/dev/null 2>&1 || true

echo "断开 $INTERFACE 当前连接..."
nmcli dev disconnect "$INTERFACE" >/dev/null 2>&1 || true

echo "创建开放热点连接..."
nmcli connection add type wifi ifname "$INTERFACE" con-name "$CON_NAME" autoconnect yes ssid "$SSID" >/dev/null
nmcli connection modify "$CON_NAME" \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    ipv4.method shared \
    ipv6.method ignore \
    wifi-sec.key-mgmt "" >/dev/null

echo "启动热点连接..."
if nmcli connection up "$CON_NAME" >/dev/null 2>&1; then
    echo ""
    echo "=========================================="
    echo "热点创建成功!"
    echo "=========================================="
    echo "SSID: $SSID"
    echo "安全模式: open (无密码)"
    echo "热点 IP: $HOTSPOT_IP"
    echo ""
    echo "当前 $INTERFACE 状态:"
    ip -4 addr show "$INTERFACE" | grep "inet " || echo "  未分配 IP"
    echo ""
    echo "Web 配置界面:"
    echo "  - 通过热点: http://$HOTSPOT_IP:5000"
    echo ""
    echo "连接详情:"
    nmcli -f GENERAL.NAME,GENERAL.DEVICES,802-11-wireless.ssid,802-11-wireless.mode,802-11-wireless-security.key-mgmt con show "$CON_NAME"
    echo ""
else
    echo "热点创建失败!"
    echo ""
    echo "可能原因:"
    echo "1. $INTERFACE 已被其他连接占用"
    echo "2. 网络接口不支持 AP 模式"
    echo "3. NetworkManager 不允许当前无线芯片创建开放热点"
    echo ""
    echo "建议排查:"
    echo "  nmcli dev status"
    echo "  nmcli -p connection show '$CON_NAME'"
    echo "  nmcli dev wifi list ifname $INTERFACE"
    exit 1
fi

echo "当前网络状态:"
nmcli dev status
