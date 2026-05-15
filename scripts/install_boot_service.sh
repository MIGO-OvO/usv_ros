#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WS_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
SERVICE_NAME="usv-boot.service"
SERVICE_PATH="/etc/systemd/system/$SERVICE_NAME"

SSID="${1:-USV_Control}"
PASSWORD="${2:-12345678}"
RUN_USER="${3:-${SUDO_USER:-$(id -un)}}"
WEB_PORT="${WEB_PORT:-5000}"
HOTSPOT_IFACE="${HOTSPOT_IFACE:-wlan0}"
HOTSPOT_CONN_NAME="${HOTSPOT_CONN_NAME:-USV_AP}"
HOTSPOT_IP="${HOTSPOT_IP:-10.42.0.1}"
USV_BOOT_WAIT_SECONDS="${USV_BOOT_WAIT_SECONDS:-90}"
USV_STRICT_SELF_CHECK="${USV_STRICT_SELF_CHECK:-true}"

log() {
    echo "[install-usv-boot] $*"
}

require_root() {
    if [[ "$(id -u)" -ne 0 ]]; then
        log "ERROR: run with sudo"
        exit 1
    fi
}

escape_systemd_env() {
    local value="$1"
    value="${value//\\/\\\\}"
    value="${value//\"/\\\"}"
    printf "%s" "$value"
}

write_service() {
    local esc_ssid esc_password esc_user esc_script_dir esc_ws_dir
    esc_ssid="$(escape_systemd_env "$SSID")"
    esc_password="$(escape_systemd_env "$PASSWORD")"
    esc_user="$(escape_systemd_env "$RUN_USER")"
    esc_script_dir="$(escape_systemd_env "$SCRIPT_DIR")"
    esc_ws_dir="$(escape_systemd_env "$WS_DIR")"

    cat > "$SERVICE_PATH" <<EOF
[Unit]
Description=USV ROS payload and Wi-Fi hotspot boot service
After=NetworkManager.service network-online.target
Wants=NetworkManager.service

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$esc_ws_dir
Environment="USV_RUN_USER=$esc_user"
Environment="USV_HOTSPOT_SSID=$esc_ssid"
Environment="USV_HOTSPOT_PASSWORD=$esc_password"
Environment="WEB_PORT=$WEB_PORT"
Environment="HOTSPOT_IFACE=$HOTSPOT_IFACE"
Environment="HOTSPOT_CONN_NAME=$HOTSPOT_CONN_NAME"
Environment="HOTSPOT_IP=$HOTSPOT_IP"
Environment="USV_BOOT_WAIT_SECONDS=$USV_BOOT_WAIT_SECONDS"
Environment="USV_STRICT_SELF_CHECK=$USV_STRICT_SELF_CHECK"
ExecStart=/usr/bin/env bash "$esc_script_dir/usv_boot_start.sh"
ExecStop=/usr/bin/env bash "$esc_script_dir/usv_boot_stop.sh"
TimeoutStartSec=180
TimeoutStopSec=60
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF
}

main() {
    require_root

    if [[ ${#PASSWORD} -lt 8 ]]; then
        log "ERROR: WPA-PSK password length must be >= 8"
        exit 1
    fi

    if ! id "$RUN_USER" >/dev/null 2>&1; then
        log "ERROR: run user does not exist: $RUN_USER"
        exit 1
    fi

    chmod +x \
        "$SCRIPT_DIR/usv_boot_start.sh" \
        "$SCRIPT_DIR/usv_boot_stop.sh" \
        "$SCRIPT_DIR/start_usv_all.sh" \
        "$SCRIPT_DIR/stop_usv_all.sh" \
        "$SCRIPT_DIR/status_usv_all.sh" \
        "$SCRIPT_DIR/setup_hotspot.sh" \
        "$SCRIPT_DIR/stop_hotspot.sh" \
        "$SCRIPT_DIR/uninstall_boot_service.sh"

    log "write $SERVICE_PATH"
    write_service

    systemctl daemon-reload
    systemctl enable --now "$SERVICE_NAME"

    log "installed and started: $SERVICE_NAME"
    log "status: sudo systemctl status $SERVICE_NAME"
    log "logs: sudo journalctl -u $SERVICE_NAME -f"
    log "boot check: $WS_DIR/.usv_run/logs/boot_check.log"
}

main "$@"
