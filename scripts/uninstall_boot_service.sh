#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="usv-boot.service"
SERVICE_PATH="/etc/systemd/system/usv-boot.service"

log() {
    echo "[uninstall-usv-boot] $*"
}

if [[ "$(id -u)" -ne 0 ]]; then
    log "ERROR: run with sudo"
    exit 1
fi

systemctl disable --now "$SERVICE_NAME" >/dev/null 2>&1 || true
rm -f "$SERVICE_PATH"
systemctl daemon-reload
systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true

log "removed $SERVICE_PATH"
