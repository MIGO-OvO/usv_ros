#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_env.sh"

load_ros_env
require_roscore
print_workspace_info

log "启动最小化系统: pump + web"
exec roslaunch usv_ros usv_bringup.launch \
    enable_mavlink_trigger:=false \
    enable_mavlink_bridge:=false \
    "$@"

