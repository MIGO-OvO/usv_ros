#!/usr/bin/env bash
set -euo pipefail

resolve_script_dir() {
    local source="${BASH_SOURCE[0]}"
    local dir

    while [[ -h "$source" ]]; do
        dir="$(cd -P "$(dirname "$source")" && pwd)"
        source="$(readlink "$source")"
        [[ "$source" != /* ]] && source="$dir/$source"
    done

    cd -P "$(dirname "$source")" && pwd
}

SCRIPT_DIR="$(resolve_script_dir)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_env.sh"

LAUNCH_PID_FILE="$RUN_DIR/usv_system.pid"

ctl_log() {
    echo "[usvctl] $*"
}

usage() {
    cat <<EOF
USV ROS command helper

Usage:
  usvctl <command> [args...]
  usvon [roslaunch args...]
  usvoff
  usvrestart [roslaunch args...]
  usvstatus
  usvupdate
  usvbuild [catkin_make args...]
  usvdeploy [roslaunch args...]

Commands:
  start      Start roscore, mavlink-routerd, and usv_ros launch
  stop       Stop usv_ros launch, mavlink-routerd, and roscore
  restart    Stop then start; extra args pass to roslaunch
  status     Print process, hotspot, ROS, MAVROS, and bridge status
  update     git pull --ff-only in the usv_ros repository
  build      Run catkin_make in the workspace root
  deploy     Stop, update, build, then start
EOF
}

is_usv_system_running() {
    if [[ ! -f "$LAUNCH_PID_FILE" ]]; then
        return 1
    fi

    local pid
    pid="$(cat "$LAUNCH_PID_FILE" 2>/dev/null || true)"
    [[ -n "$pid" ]] && is_pid_running "$pid"
}

require_system_stopped() {
    local action="$1"
    if is_usv_system_running; then
        local pid
        pid="$(cat "$LAUNCH_PID_FILE")"
        ctl_log "ERROR: cannot $action while usv_system is running (pid=$pid)"
        ctl_log "Run 'usvoff' first, or use 'usvdeploy' for stop -> update -> build -> start."
        exit 1
    fi
}

start_system() {
    "$SCRIPT_DIR/start_usv_all.sh" "$@"
}

stop_system() {
    "$SCRIPT_DIR/stop_usv_all.sh"
}

restart_system() {
    "$SCRIPT_DIR/restart_usv_all.sh" "$@"
}

status_system() {
    "$SCRIPT_DIR/status_usv_all.sh"
}

update_system() {
    require_system_stopped "update"
    ctl_log "update repo: $PKG_DIR"
    git -C "$PKG_DIR" pull --ff-only
}

build_system() {
    require_system_stopped "build"
    ensure_file "$ROS_SETUP" "Install ROS Noetic before running usvbuild."

    ctl_log "build workspace: $WS_DIR"
    set +u
    # shellcheck disable=SC1090
    source "$ROS_SETUP"
    set -u

    (cd "$WS_DIR" && catkin_make "$@")
}

deploy_system() {
    stop_system
    update_system
    build_system
    start_system "$@"
}

dispatch() {
    local invoked
    local cmd
    invoked="$(basename "$0")"

    case "$invoked" in
        usvon)
            cmd="start"
            ;;
        usvoff)
            cmd="stop"
            ;;
        usvrestart)
            cmd="restart"
            ;;
        usvstatus)
            cmd="status"
            ;;
        usvupdate)
            cmd="update"
            ;;
        usvbuild)
            cmd="build"
            ;;
        usvdeploy)
            cmd="deploy"
            ;;
        usvctl)
            cmd="${1:-help}"
            if [[ $# -gt 0 ]]; then
                shift
            fi
            ;;
        *)
            cmd="${1:-help}"
            if [[ $# -gt 0 ]]; then
                shift
            fi
            ;;
    esac

    case "$cmd" in
        start|on)
            start_system "$@"
            ;;
        stop|off)
            stop_system
            ;;
        restart)
            restart_system "$@"
            ;;
        status)
            status_system
            ;;
        update)
            update_system
            ;;
        build)
            build_system "$@"
            ;;
        deploy)
            deploy_system "$@"
            ;;
        help|-h|--help)
            usage
            ;;
        *)
            ctl_log "ERROR: unknown command: $cmd"
            usage
            exit 2
            ;;
    esac
}

dispatch "$@"
