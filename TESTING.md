# USV ROS 系统测试指南

## 快速测试 Web 配置服务器

### 步骤 1: SSH 连接到 Nano

```bash
ssh jetson@10.33.106.36
```

### 步骤 2: 运行诊断脚本

```bash
cd ~/usv_ws
./src/usv_ros/scripts/test_web_server.sh
```

这个脚本会检查：
- Web 服务器进程是否运行
- 端口 5000 是否监听
- Flask 依赖是否安装
- ROS 环境是否配置

### 步骤 3: 启动系统 (如果未运行)

#### 方法 A: 完整启动 (推荐)

```bash
# 在 Nano 上执行
cd ~/usv_ws
source devel/setup.bash
roslaunch usv_ros usv_bringup.launch
```

#### 方法 B: 仅启动 Web 服务器 (快速测试)

```bash
# 终端 1: 启动 roscore
roscore

# 终端 2: 启动 Web 服务器
cd ~/usv_ws
source devel/setup.bash
rosrun usv_ros web_config_server.py
```

#### 方法 C: 独立运行 (不依赖 ROS，调试用)

```bash
cd ~/usv_ws
source devel/setup.bash
python3 src/usv_ros/scripts/web_config_server.py
```

### 步骤 4: 测试访问

#### 在 Nano 上测试 (SSH 终端)

```bash
# 测试本地访问
curl http://localhost:5000

# 测试网络接口访问
curl http://10.33.106.36:5000

# 如果成功，应该返回 HTML 页面内容
```

#### 在开发电脑上测试

**方法 1: 直接访问 (推荐)**

在浏览器打开: `http://10.33.106.36:5000`

**方法 2: 命令行测试**

```bash
# 在开发电脑上执行
curl http://10.33.106.36:5000

# 测试 API
curl http://10.33.106.36:5000/api/status
curl http://10.33.106.36:5000/api/config
```

**方法 3: SSH 端口转发**

```bash
# 在开发电脑上执行
ssh -L 5000:localhost:5000 jetson@10.33.106.36

# 保持 SSH 连接，然后在浏览器访问:
# http://localhost:5000
```

---

## 常见问题排查

### 问题 1: "无法访问" 或 "连接被拒绝"

**可能原因**:
1. Web 服务器未启动
2. 端口 5000 被占用
3. 防火墙阻止
4. ROS 环境未配置

**解决方案**:

```bash
# 1. 检查进程
ps aux | grep web_config_server

# 2. 检查端口
netstat -tuln | grep 5000
# 或
ss -tuln | grep 5000

# 3. 检查防火墙 (如果启用)
sudo ufw status
sudo ufw allow 5000/tcp

# 4. 检查 ROS 环境
echo $ROS_MASTER_URI
# 如果为空，运行:
source ~/usv_ws/devel/setup.bash

# 5. 查看日志
rosnode list | grep web_config
rosnode info /web_config_server
```

### 问题 2: Flask 未安装

```bash
# 安装 Flask
pip3 install flask flask-cors

# 验证安装
python3 -c "import flask; print(flask.__version__)"
```

### 问题 3: 端口被占用

```bash
# 查看占用端口 5000 的进程
sudo lsof -i :5000

# 或
sudo netstat -tulnp | grep 5000

# 杀死占用进程
sudo kill -9 <PID>
```

### 问题 4: 权限问题

```bash
# 确保脚本有执行权限
chmod +x ~/usv_ws/src/usv_ros/scripts/web_config_server.py

# 确保配置目录可写
mkdir -p ~/usv_ws/config
chmod 755 ~/usv_ws/config
```

---

## 测试 API 接口

### 获取配置

```bash
curl http://10.33.106.36:5000/api/config | python3 -m json.tool
```

### 获取状态

```bash
curl http://10.33.106.36:5000/api/status | python3 -m json.tool
```

### 保存配置

```bash
curl -X POST http://10.33.106.36:5000/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "mission": {"name": "测试任务"},
    "pump_settings": {"pid_mode": true, "pid_precision": 0.1},
    "sampling_sequence": {
      "loop_count": 1,
      "steps": [
        {
          "name": "测试步骤",
          "X": {"enable": "E", "direction": "F", "speed": "5", "angle": "90"},
          "Y": {"enable": "D"},
          "Z": {"enable": "D"},
          "A": {"enable": "D"},
          "interval": 1000
        }
      ]
    }
  }'
```

### 启动采样

```bash
curl -X POST http://10.33.106.36:5000/api/mission/start
```

### 停止采样

```bash
curl -X POST http://10.33.106.36:5000/api/mission/stop
```

---

## 完整测试流程

### 1. 环境准备

```bash
# SSH 连接到 Nano
ssh jetson@10.33.106.36

# 进入工作空间
cd ~/usv_ws

# 配置 ROS 环境
source devel/setup.bash

# 检查编译
catkin_make
```

### 2. 启动系统

```bash
# 启动完整系统
roslaunch usv_ros usv_bringup.launch
```

### 3. 验证节点运行

```bash
# 新开一个 SSH 终端
ssh jetson@10.33.106.36
source ~/usv_ws/devel/setup.bash

# 查看运行的节点
rosnode list

# 应该看到:
# /pump_control_node
# /web_config_server
# /mavlink_trigger_node
# /spectrometer_node (如果启用)
```

### 4. 测试 Web 界面

在开发电脑浏览器打开: `http://10.33.106.36:5000`

应该看到:
- 系统状态显示
- 任务配置表单
- 采样步骤编辑器
- 实时日志

### 5. 测试泵控制

```bash
# 发送测试指令
rostopic pub /usv/pump_command std_msgs/String "data: 'XEFR90.0P0.1'"

# 查看角度反馈
rostopic echo /usv/pump_angles

# 停止
rosservice call /usv/pump_stop
```

### 6. 测试自动化

```bash
# 通过 Web 界面配置步骤后，启动自动化
rosservice call /usv/automation_start

# 查看状态
rostopic echo /usv/pump_status

# 停止
rosservice call /usv/automation_stop
```

---

## 日志查看

### ROS 日志

```bash
# 查看所有日志
roscd log
tail -f *.log

# 查看特定节点日志
rosnode info /web_config_server
```

### Web 服务器日志

Web 服务器的日志会输出到启动它的终端，或者在 launch 文件中查看。

```bash
# 如果通过 launch 启动，查看输出
roslaunch usv_ros usv_bringup.launch

# 日志会显示:
# [INFO] Web Config Server initialized
# [INFO] Web server started at http://0.0.0.0:5000
```

---

## 性能测试

### 测试并发请求

```bash
# 安装 Apache Bench (可选)
sudo apt install apache2-utils

# 测试 100 个请求
ab -n 100 -c 10 http://10.33.106.36:5000/api/status
```

### 测试响应时间

```bash
# 使用 curl 测试
time curl http://10.33.106.36:5000/api/config
```

---

## 网络诊断

### 检查网络连通性

```bash
# 从开发电脑 ping Nano
ping 10.33.106.36

# 检查端口是否可达
telnet 10.33.106.36 5000
# 或
nc -zv 10.33.106.36 5000
```

### 检查路由

```bash
# 在 Nano 上
ip route show

# 在开发电脑上
traceroute 10.33.106.36
```

---

## 自动化测试脚本

创建一个测试脚本 `test_all.sh`:

```bash
#!/bin/bash
# 完整系统测试脚本

echo "开始 USV 系统测试..."

# 1. 测试 Web 服务器
echo "1. 测试 Web 服务器..."
curl -s http://10.33.106.36:5000 > /dev/null && echo "✓ Web 服务器正常" || echo "✗ Web 服务器异常"

# 2. 测试 API
echo "2. 测试 API..."
curl -s http://10.33.106.36:5000/api/status > /dev/null && echo "✓ API 正常" || echo "✗ API 异常"

# 3. 测试 ROS 节点
echo "3. 测试 ROS 节点..."
rosnode list | grep -q web_config_server && echo "✓ Web 节点运行中" || echo "✗ Web 节点未运行"
rosnode list | grep -q pump_control_node && echo "✓ 泵控制节点运行中" || echo "✗ 泵控制节点未运行"

echo "测试完成!"
```

---

## 故障恢复

### 重启所有服务

```bash
# 1. 停止所有 ROS 节点
rosnode kill -a

# 2. 停止 roscore
killall roscore rosmaster

# 3. 等待几秒
sleep 3

# 4. 重新启动
roslaunch usv_ros usv_bringup.launch
```

### 清理配置

```bash
# 删除配置文件 (重置为默认)
rm ~/usv_ws/config/sampling_config.json

# 重启 Web 服务器，会自动创建默认配置
```
