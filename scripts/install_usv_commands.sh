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
USVCTL_SCRIPT="$SCRIPT_DIR/usvctl.sh"
TARGET_DIR="${USV_COMMAND_DIR:-$HOME/.local/bin}"
COMMANDS=(usvctl usvon usvoff usvrestart usvstatus usvhotspot usvaddr usvupdate usvimport usvbuild usvdeploy)

log() {
    echo "[install-usv-commands] $*"
}

check_path() {
    case ":$PATH:" in
        *":$TARGET_DIR:"*)
            ;;
        *)
            log "WARNING: $TARGET_DIR is not in PATH"
            log "Add this to ~/.bashrc, then reopen shell:"
            if [[ "$TARGET_DIR" == "$HOME/.local/bin" ]]; then
                echo 'export PATH="$HOME/.local/bin:$PATH"'
            else
                echo "export PATH=\"$TARGET_DIR:\$PATH\""
            fi
            ;;
    esac
}

install_one() {
    local name="$1"
    local target="$TARGET_DIR/$name"
    local current=""

    if [[ -L "$target" ]]; then
        current="$(readlink "$target")"
        if [[ "$current" != "$USVCTL_SCRIPT" ]]; then
            log "ERROR: $target already points to $current"
            exit 1
        fi
    elif [[ -e "$target" ]]; then
        log "ERROR: $target already exists and is not a symlink"
        exit 1
    fi

    ln -sfn "$USVCTL_SCRIPT" "$target"
    log "linked $target -> $USVCTL_SCRIPT"
}

install_commands() {
    if [[ ! -f "$USVCTL_SCRIPT" ]]; then
        log "ERROR: missing $USVCTL_SCRIPT"
        exit 1
    fi

    mkdir -p "$TARGET_DIR"
    chmod +x "$USVCTL_SCRIPT"

    local cmd
    for cmd in "${COMMANDS[@]}"; do
        install_one "$cmd"
    done

    check_path
    log "installed"
}

uninstall_commands() {
    local cmd
    local target
    local current

    for cmd in "${COMMANDS[@]}"; do
        target="$TARGET_DIR/$cmd"
        if [[ -L "$target" ]]; then
            current="$(readlink "$target")"
            if [[ "$current" == "$USVCTL_SCRIPT" ]]; then
                rm -f "$target"
                log "removed $target"
            else
                log "skip $target: points to $current"
            fi
        elif [[ -e "$target" ]]; then
            log "skip $target: not a symlink"
        fi
    done
}

usage() {
    cat <<EOF
Usage:
  install_usv_commands.sh install
  install_usv_commands.sh uninstall

Environment:
  USV_COMMAND_DIR   target directory, default: \$HOME/.local/bin
EOF
}

main() {
    local action="${1:-install}"
    case "$action" in
        install)
            install_commands
            ;;
        uninstall)
            uninstall_commands
            ;;
        help|-h|--help)
            usage
            ;;
        *)
            log "ERROR: unknown action: $action"
            usage
            exit 2
            ;;
    esac
}

main "$@"
