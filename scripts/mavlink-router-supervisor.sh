#!/usr/bin/env bash
# Keep the router running; the PID file belongs to this supervisor.
set -uo pipefail

child=""
stop_router() {
    trap '' TERM INT
    if [[ -n "$child" ]]; then
        kill "$child" 2>/dev/null || true
        wait "$child" 2>/dev/null || true
    fi
    exit 0
}
trap stop_router TERM INT

delay=2
while true; do
    started=$SECONDS
    "$@" &
    child=$!
    wait "$child"
    result=$?
    child=""
    if (( SECONDS - started >= 60 )); then
        delay=2
    fi
    echo "[usv-router] exited status=$result; retry in ${delay}s"
    sleep "$delay" &
    child=$!
    wait "$child"
    child=""
    delay=$((delay < 30 ? delay * 2 : 30))
    (( delay > 30 )) && delay=30
done
