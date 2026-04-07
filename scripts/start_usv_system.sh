#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_env.sh"

load_ros_env
require_roscore
start_mavlink_router
print_workspace_info

log "启动 usv_ros 主系统 (router 已就绪)"
exec roslaunch usv_ros usv_bringup.launch "$@"

