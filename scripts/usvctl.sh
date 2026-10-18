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
  usvhotspot on|off|status [ssid] [password]
  usvaddr
  usvupdate
  usvimport [bundle_path] [--build] [--restart]
  usvbuild [catkin_make args...]
  usvdeploy [roslaunch args...]

Commands:
  start      Start roscore, mavlink-routerd, and usv_ros launch
  stop       Stop usv_ros launch, mavlink-routerd, and roscore
  restart    Stop then start; extra args pass to roslaunch
  status     Print process, hotspot, ROS, MAVROS, and bridge status
  hotspot    Start, stop, or inspect the USV Wi-Fi hotspot
  addr       Print SSH and Web access commands without ROS diagnostics
  update     Back up local changes with git stash, then git pull --ff-only
  import     import a git bundle created by create_hotspot_update.bat
  build      Run catkin_make in the workspace root
  deploy     Stop, update, chmod runtime scripts, build, then start
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
        ctl_log "Run 'usvoff' first, or use 'usvdeploy' for stop -> update -> chmod -> build -> start."
        exit 1
    fi
}

start_system() {
    bash "$SCRIPT_DIR/start_usv_all.sh" "$@"
}

stop_system() {
    bash "$SCRIPT_DIR/stop_usv_all.sh"
}

restart_system() {
    bash "$SCRIPT_DIR/restart_usv_all.sh" "$@"
}

status_system() {
    bash "$SCRIPT_DIR/status_usv_all.sh"
}

hotspot_system() {
    local action="${1:-status}"
    if [[ $# -gt 0 ]]; then
        shift
    fi

    case "$action" in
        on|start)
            if [[ "$(id -u)" -eq 0 ]]; then
                bash "$SCRIPT_DIR/setup_hotspot.sh" "$@"
            else
                sudo -E bash "$SCRIPT_DIR/setup_hotspot.sh" "$@"
            fi
            ;;
        off|stop)
            if [[ "$(id -u)" -eq 0 ]]; then
                bash "$SCRIPT_DIR/stop_hotspot.sh"
            else
                sudo -E bash "$SCRIPT_DIR/stop_hotspot.sh"
            fi
            ;;
        status)
            bash "$SCRIPT_DIR/status_usv_all.sh" hotspot
            ;;
        *)
            ctl_log "ERROR: unknown hotspot action: $action"
            ctl_log "Usage: usvhotspot on|off|status [ssid] [password]"
            exit 2
            ;;
    esac
}

addr_system() {
    bash "$SCRIPT_DIR/status_usv_all.sh" addr
}

ensure_scripts_executable() {
    ctl_log "ensure script permissions: $SCRIPT_DIR/*.sh $SCRIPT_DIR/*.py $SCRIPT_DIR/map_resources/*"
    chmod +x "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/*.py "$SCRIPT_DIR"/map_resources/*.sh "$SCRIPT_DIR"/map_resources/*.py
}

update_system() {
    require_system_stopped "update"
    ctl_log "update repo: $PKG_DIR"

    local repo_status
    local backup_ref
    repo_status="$(git -C "$PKG_DIR" status --porcelain --untracked-files=all)"
    if [[ -n "$repo_status" ]]; then
        ctl_log "back up local changes (including untracked files) before update"
        if ! git -C "$PKG_DIR" stash push --include-untracked \
            -m "usvctl auto-stash before update $(date -u +%Y-%m-%dT%H:%M:%SZ)"; then
            ctl_log "ERROR: could not stash local changes; update/deploy stopped."
            return 1
        fi

        backup_ref="$(git -C "$PKG_DIR" rev-parse refs/stash)"
        ctl_log "local changes saved in stash: $backup_ref (kept; not automatically restored)"
        ctl_log "inspect backup: git -C \"$PKG_DIR\" stash show --stat $backup_ref"
        ctl_log "restore manually (may conflict): git -C \"$PKG_DIR\" stash apply --index $backup_ref"

        repo_status="$(git -C "$PKG_DIR" status --porcelain --untracked-files=all)"
        if [[ -n "$repo_status" ]]; then
            ctl_log "ERROR: local changes remain after stashing; update/deploy stopped."
            return 1
        fi
    fi

    if ! git -C "$PKG_DIR" pull --ff-only; then
        ctl_log "ERROR: git pull --ff-only failed; update/deploy stopped. Any auto-stash above remains saved."
        return 1
    fi
}

import_system() {
    bash "$SCRIPT_DIR/import_hotspot_update.sh" "$@"
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
    ensure_scripts_executable
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
        usvhotspot)
            cmd="hotspot"
            ;;
        usvaddr)
            cmd="addr"
            ;;
        usvupdate)
            cmd="update"
            ;;
        usvimport)
            cmd="import"
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
        hotspot|ap)
            hotspot_system "$@"
            ;;
        addr|address)
            addr_system
            ;;
        update)
            update_system
            ;;
        import)
            import_system "$@"
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
