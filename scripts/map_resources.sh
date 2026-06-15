#!/usr/bin/env bash
set -euo pipefail

# Interactive offline map resource helper for Linux/Jetson.
# Actions: download/export, inspect, import. Packs auto-detected from common dirs.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PACK_DIR="${USV_MAP_PACK_DIR:-$REPO_DIR/.map_packs}"
DEFAULT_CACHE="${USV_MAP_CACHE:-$HOME/usv_ws/map_cache}"
PYTHON_BIN="${PYTHON:-python3}"
mkdir -p "$PACK_DIR"

log() { echo "[mapres] $*"; }

select_pack() {
    mapfile -t packs < <(
        for d in "$PACK_DIR" "$PWD" "$HOME/Downloads" "$HOME/usv_ws"; do
            [[ -d "$d" ]] || continue
            find "$d" -type f \( -name '*.tar' -o -name '*.pack' \) -printf '%T@ %p\n' 2>/dev/null || true
        done | sort -nr | awk '!seen[$2]++ {sub(/^[^ ]+ /, ""); print}'
    )
    if [[ ${#packs[@]} -eq 0 ]]; then
        log "No .tar/.pack found. Put packs in: $PACK_DIR"
        return 1
    fi
    local i
    for i in "${!packs[@]}"; do
        printf '%d) %s\n' "$((i + 1))" "${packs[$i]}"
    done
    local idx
    read -r -p "Pick pack number: " idx
    [[ "$idx" =~ ^[0-9]+$ ]] || return 1
    (( idx >= 1 && idx <= ${#packs[@]} )) || return 1
    PICKED_PACK="${packs[$((idx - 1))]}"
}

download_pack() {
    log "pack dir=$PACK_DIR"
    (cd "$PACK_DIR" && "$PYTHON_BIN" "$SCRIPT_DIR/map_pack_export.py" -i)
}

export_cache() {
    local cache_dir out stamp
    read -r -p "Cache dir [$DEFAULT_CACHE]: " cache_dir
    cache_dir="${cache_dir:-$DEFAULT_CACHE}"
    stamp="$(date +%Y%m%d_%H%M%S)"
    out="$PACK_DIR/map_cache_$stamp.tar"
    read -r -p "Output pack [$out]: " out_input
    out="${out_input:-$out}"
    "$PYTHON_BIN" "$SCRIPT_DIR/map_pack_export.py" --from-cache "$cache_dir" --out "$out"
}

inspect_pack() {
    select_pack || return 0
    "$PYTHON_BIN" "$SCRIPT_DIR/map_pack_import.py" "$PICKED_PACK" --inspect
}

import_pack() {
    select_pack || return 0
    local cache_dir
    read -r -p "Import cache dir [$DEFAULT_CACHE]: " cache_dir
    cache_dir="${cache_dir:-$DEFAULT_CACHE}"
    "$PYTHON_BIN" "$SCRIPT_DIR/map_pack_import.py" "$PICKED_PACK" --cache-dir "$cache_dir"
}

while true; do
    cat <<EOF

===== USV Map Resources =====
pack dir: $PACK_DIR
1) download map pack (interactive bbox/zoom)
2) inspect available pack
3) import available pack
4) export from existing cache
q) quit
EOF
    read -r -p "Select: " choice
    case "$choice" in
        1) download_pack ;;
        2) inspect_pack ;;
        3) import_pack ;;
        4) export_cache ;;
        q|Q) exit 0 ;;
        *) log "invalid selection" ;;
    esac
done
