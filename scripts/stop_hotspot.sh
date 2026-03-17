#!/bin/bash
# USV Wi-Fi Hotspot Stop Script
# 关闭 USV_AP 热点连接，并尽量释放 wlan0。

set -euo pipefail

CON_NAME="${HOTSPOT_CONN_NAME:-USV_AP}"
INTERFACE="${HOTSPOT_IFACE:-wlan0}"

echo "=========================================="
echo "USV Wi-Fi Hotspot Stop"
echo "=========================================="
echo "Connection: $CON_NAME"
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

if nmcli -t -f NAME con show --active 2>/dev/null | grep -Fxq "$CON_NAME"; then
    echo "关闭热点连接..."
    nmcli connection down "$CON_NAME" >/dev/null || true
else
    echo "热点连接 $CON_NAME 当前未激活"
fi

if nmcli -t -f DEVICE,STATE dev status 2>/dev/null | grep -Eq "^${INTERFACE}:connected|^${INTERFACE}:connecting|^${INTERFACE}:disconnected"; then
    echo "断开接口 $INTERFACE..."
    nmcli device disconnect "$INTERFACE" >/dev/null 2>&1 || true
fi

echo "当前网络状态:"
nmcli dev status

echo "热点关闭完成"

