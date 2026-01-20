#!/bin/bash
# USV Wi-Fi Hotspot Setup Script
# 在 Jetson Nano 上创建 Wi-Fi 热点 (无密码)
#
# 使用方法:
#   sudo ./setup_hotspot.sh [SSID]
#
# 默认:
#   SSID: USV_Control
#   无密码 (开放网络)

SSID="${1:-USV_Control}"
INTERFACE="wlan0"
CON_NAME="USV_AP"

echo "=========================================="
echo "USV Wi-Fi Hotspot Setup"
echo "=========================================="
echo "SSID: $SSID"
echo "密码: 无 (开放网络)"
echo "Interface: $INTERFACE"
echo ""

# 检查是否为 root
if [ "$EUID" -ne 0 ]; then
    echo "请使用 sudo 运行此脚本"
    exit 1
fi

# 检查 NetworkManager
if ! command -v nmcli &> /dev/null; then
    echo "错误: nmcli 未安装"
    exit 1
fi

# 删除已存在的连接
echo "清理旧连接..."
nmcli con delete "$CON_NAME" 2>/dev/null

# 创建热点 (无密码)
echo "创建开放热点..."
nmcli dev wifi hotspot ifname "$INTERFACE" con-name "$CON_NAME" ssid "$SSID"

if [ $? -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "热点创建成功!"
    echo "=========================================="
    echo "SSID: $SSID"
    echo "密码: 无 (开放网络)"
    echo "热点 IP: 10.42.0.1"
    echo ""
    echo "注意: 如果 wlan0 已连接到其他网络，热点可能无法启动"
    echo "当前 wlan0 状态:"
    ip addr show wlan0 | grep "inet " || echo "  未分配 IP"
    echo ""
    echo "Web 配置界面:"
    echo "  - 通过热点: http://10.42.0.1:5000"
    echo "  - 通过 SSH: http://10.33.106.36:5000"
    echo ""
    echo "SSH 访问:"
    echo "  ssh jetson@10.33.106.36"
    echo ""
    echo "设置开机自启:"
    echo "  nmcli con mod '$CON_NAME' connection.autoconnect yes"
    echo ""
else
    echo "热点创建失败!"
    echo ""
    echo "可能原因:"
    echo "1. wlan0 已连接到其他网络 (需要断开)"
    echo "2. 网络接口不支持 AP 模式"
    echo ""
    echo "解决方案:"
    echo "  # 断开当前 Wi-Fi 连接"
    echo "  nmcli dev disconnect wlan0"
    echo "  # 然后重新运行此脚本"
    exit 1
fi

# 显示连接状态
echo "当前网络状态:"
nmcli dev status
