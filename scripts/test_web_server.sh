#!/bin/bash
# Web 服务器测试脚本
# 用于检查 Web 配置服务器是否正常运行

echo "=========================================="
echo "USV Web 服务器诊断工具"
echo "=========================================="
echo ""

# 1. 检查 ROS 节点是否运行
echo "1. 检查 ROS 节点状态..."
if pgrep -f "web_config_server.py" > /dev/null; then
    echo "   ✓ web_config_server.py 进程正在运行"
    ps aux | grep web_config_server.py | grep -v grep
else
    echo "   ✗ web_config_server.py 未运行"
    echo "   启动命令: rosrun usv_ros web_config_server.py"
fi
echo ""

# 2. 检查端口是否监听
echo "2. 检查端口 5000 监听状态..."
if netstat -tuln 2>/dev/null | grep ":5000" > /dev/null || ss -tuln 2>/dev/null | grep ":5000" > /dev/null; then
    echo "   ✓ 端口 5000 正在监听"
    netstat -tuln 2>/dev/null | grep ":5000" || ss -tuln 2>/dev/null | grep ":5000"
else
    echo "   ✗ 端口 5000 未监听"
fi
echo ""

# 3. 检查防火墙
echo "3. 检查防火墙状态..."
if command -v ufw &> /dev/null; then
    sudo ufw status | grep "5000" || echo "   端口 5000 未在防火墙规则中"
else
    echo "   ufw 未安装，跳过防火墙检查"
fi
echo ""

# 4. 测试本地访问
echo "4. 测试本地 HTTP 访问..."
if command -v curl &> /dev/null; then
    echo "   测试 localhost:5000..."
    if curl -s -o /dev/null -w "%{http_code}" http://localhost:5000 | grep -q "200\|301\|302"; then
        echo "   ✓ 本地访问成功"
    else
        echo "   ✗ 本地访问失败"
        echo "   响应码: $(curl -s -o /dev/null -w '%{http_code}' http://localhost:5000)"
    fi
else
    echo "   curl 未安装，跳过 HTTP 测试"
fi
echo ""

# 5. 检查网络接口
echo "5. 检查网络接口和 IP 地址..."
ip addr show | grep -E "inet |wlan0|eth0" | grep -v "127.0.0.1"
echo ""

# 6. 检查 Flask 依赖
echo "6. 检查 Python 依赖..."
python3 -c "import flask; print('   ✓ Flask 版本:', flask.__version__)" 2>/dev/null || echo "   ✗ Flask 未安装"
python3 -c "import flask_cors; print('   ✓ Flask-CORS 已安装')" 2>/dev/null || echo "   ✗ Flask-CORS 未安装"
echo ""

# 7. 检查 ROS 环境
echo "7. 检查 ROS 环境..."
if [ -n "$ROS_MASTER_URI" ]; then
    echo "   ✓ ROS 环境已配置"
    echo "   ROS_MASTER_URI: $ROS_MASTER_URI"
else
    echo "   ✗ ROS 环境未配置"
    echo "   运行: source ~/usv_ws/devel/setup.bash"
fi
echo ""

# 8. 提供测试命令
echo "=========================================="
echo "测试命令"
echo "=========================================="
echo ""
echo "# 在 Nano 上测试 (SSH 连接后执行):"
echo "curl http://localhost:5000"
echo "curl http://10.33.106.36:5000"
echo ""
echo "# 在开发电脑上测试:"
echo "curl http://10.33.106.36:5000"
echo ""
echo "# 或在浏览器访问:"
echo "http://10.33.106.36:5000"
echo ""
echo "=========================================="
echo "启动 Web 服务器"
echo "=========================================="
echo ""
echo "# 方法1: 通过 launch 文件启动 (推荐)"
echo "roslaunch usv_ros usv_bringup.launch"
echo ""
echo "# 方法2: 单独启动 Web 服务器"
echo "rosrun usv_ros web_config_server.py"
echo ""
echo "# 方法3: 直接运行 Python 脚本 (调试用)"
echo "cd ~/usv_ws"
echo "source devel/setup.bash"
echo "python3 src/usv_ros/scripts/web_config_server.py"
echo ""
