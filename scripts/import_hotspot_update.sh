#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_env.sh"

BUNDLE_PATH="$HOME/usv_ros_update.bundle"
REMOTE_REF="${USV_IMPORT_REF:-update_from_bundle}"
DO_BUILD="false"
DO_RESTART="false"
ALLOW_DIRTY="false"

log_import() {
    echo "[usvimport] $*"
}

usage() {
    cat <<EOF
Usage:
  usvimport [bundle_path] [--build] [--restart] [--allow-dirty]

Default bundle_path: ~/usv_ros_update.bundle

Examples:
  usvoff
  usvimport
  usvbuild
  usvon

  usvimport ~/usv_ros_update.bundle --build --restart

Options:
  --build        run catkin_make after importing
  --restart      stop before import, start after import
  --allow-dirty  allow local uncommitted files (not recommended)
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --build)
            DO_BUILD="true"
            ;;
        --restart)
            DO_RESTART="true"
            ;;
        --allow-dirty)
            ALLOW_DIRTY="true"
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            BUNDLE_PATH="$1"
            ;;
    esac
    shift
done

BUNDLE_PATH="$(readlink -f "$BUNDLE_PATH" 2>/dev/null || echo "$BUNDLE_PATH")"
if [[ ! -f "$BUNDLE_PATH" ]]; then
    log_import "ERROR: bundle not found: $BUNDLE_PATH"
    exit 2
fi

if [[ "$DO_RESTART" == "true" ]]; then
    "$SCRIPT_DIR/stop_usv_all.sh"
else
    if [[ -f "$RUN_DIR/usv_system.pid" ]]; then
        pid="$(cat "$RUN_DIR/usv_system.pid" 2>/dev/null || true)"
        if [[ -n "$pid" ]] && is_pid_running "$pid"; then
            log_import "ERROR: system is running. Run usvoff first, or use --restart."
            exit 1
        fi
    fi
fi

if [[ "$ALLOW_DIRTY" != "true" ]]; then
    if ! git -C "$PKG_DIR" diff --quiet || ! git -C "$PKG_DIR" diff --cached --quiet; then
        log_import "ERROR: local repo has uncommitted changes: $PKG_DIR"
        log_import "Run git status, commit/stash changes, or pass --allow-dirty."
        exit 1
    fi
fi

log_import "repo=$PKG_DIR"
log_import "bundle=$BUNDLE_PATH"
log_import "verify bundle"
git -C "$PKG_DIR" bundle verify "$BUNDLE_PATH"

log_import "fetch bundle -> $REMOTE_REF"
git -C "$PKG_DIR" fetch "$BUNDLE_PATH" "+HEAD:$REMOTE_REF"

log_import "merge --ff-only $REMOTE_REF"
git -C "$PKG_DIR" merge --ff-only "$REMOTE_REF"

chmod +x "$SCRIPT_DIR"/*.sh "$SCRIPT_DIR"/*.py "$SCRIPT_DIR"/map_resources/*.sh "$SCRIPT_DIR"/map_resources/*.py || true

if [[ "$DO_BUILD" == "true" ]]; then
    ensure_file "$ROS_SETUP" "Install ROS Noetic before building."
    set +u
    # shellcheck disable=SC1090
    source "$ROS_SETUP"
    set -u
    log_import "catkin_make: $WS_DIR"
    (cd "$WS_DIR" && catkin_make)
fi

if [[ "$DO_RESTART" == "true" ]]; then
    "$SCRIPT_DIR/start_usv_all.sh"
fi

log_import "done"
