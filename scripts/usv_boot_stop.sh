#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/common_env.sh"

SERVICE_NAME="${SERVICE_NAME:-usv-boot.service}"
USV_RUN_USER="${USV_RUN_USER:-${SUDO_USER:-$(id -un)}}"
USV_ENABLE_HOTSPOT="${USV_ENABLE_HOTSPOT:-false}"
BOOT_CHECK_LOG_FILE="${BOOT_CHECK_LOG_FILE:-$LOG_DIR/boot_check.log}"

prepare_boot_log() {
    mkdir -p "$RUN_DIR" "$LOG_DIR"
    touch "$BOOT_CHECK_LOG_FILE"
    if [[ "$(id -u)" -eq 0 && "$USV_RUN_USER" != "root" ]]; then
        chown -R "$USV_RUN_USER:$USV_RUN_USER" "$RUN_DIR" >/dev/null 2>&1 || true
    fi
}

log_boot() {
    echo "[$SERVICE_NAME] $*" | tee -a "$BOOT_CHECK_LOG_FILE"
}

is_hotspot_enabled() {
    [[ "$USV_ENABLE_HOTSPOT" == "true" ]]
}

run_as_usv_user() {
    local env_args=(
        "WEB_PORT=${WEB_PORT:-5000}"
        "USV_ENABLE_HOTSPOT=$USV_ENABLE_HOTSPOT"
        "HOTSPOT_IFACE=${HOTSPOT_IFACE:-wlan0}"
        "HOTSPOT_IP=${HOTSPOT_IP:-10.42.0.1}"
        "HOTSPOT_CONN_NAME=${HOTSPOT_CONN_NAME:-USV_AP}"
    )

    if [[ "$(id -u)" -eq 0 && "$USV_RUN_USER" != "root" ]]; then
        runuser -u "$USV_RUN_USER" -- env "${env_args[@]}" "$@"
    else
        env "${env_args[@]}" "$@"
    fi
}

main() {
    prepare_boot_log
    log_boot "boot stop"

    run_as_usv_user "$SCRIPT_DIR/stop_usv_all.sh" | tee -a "$BOOT_CHECK_LOG_FILE" || true

    if ! is_hotspot_enabled; then
        log_boot "skip hotspot stop: disabled"
    elif [[ "$(id -u)" -eq 0 ]]; then
        "$SCRIPT_DIR/stop_hotspot.sh" | tee -a "$BOOT_CHECK_LOG_FILE" || true
    else
        log_boot "skip hotspot stop: root required"
    fi

    log_boot "boot stop complete"
}

main "$@"
