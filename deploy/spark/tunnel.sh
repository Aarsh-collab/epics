#!/bin/bash
# Public HTTPS: explicit local Funnel opt-in, otherwise original quick tunnel.
set -euo pipefail
APP=${EPICS_APP_DIR:-$HOME/epics-app}
PORT=${EPICS_PORT:-8090}
SCRIPTS=$(cd "$(dirname "$0")" && pwd)
MODE=quick
if [[ $# == 0 ]]; then
    if [[ -e "$APP/tunnel-mode" ]]; then MODE=$(cat "$APP/tunnel-mode"); fi
elif [[ $# == 1 && "$1" == --quick ]]; then
    MODE=quick
elif [[ $# == 1 && "$1" == --funnel ]]; then
    MODE=funnel
else
    echo "usage: bash tunnel.sh [--quick|--funnel]" >&2; exit 2
fi
case "$MODE" in
    funnel) exec bash "$SCRIPTS/tunnel-funnel.sh" start ;;
    quick) ;;
    *) echo "tunnel-mode must contain quick or funnel" >&2; exit 2 ;;
esac
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && ((10#$PORT > 0 && 10#$PORT <= 65535)) || {
    echo "invalid EPICS_PORT" >&2; exit 2;
}
# Disable only a verified, recorded EPICS route. Never reset shared Tailscale.
if [[ -f "$APP/funnel-state.json" ]]; then
    bash "$SCRIPTS/tunnel-funnel.sh" off
fi
rm -f "$APP/public-url"
docker rm -f epics-tunnel >/dev/null 2>&1 || true
docker run -d --name epics-tunnel --restart unless-stopped --network host \
    --user "$(id -u):$(id -g)" -v "$APP/bin":/opt/cloudflared:ro \
    python:3.12-slim /opt/cloudflared/cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >/dev/null
for _ in $(seq 60); do
    url=$(docker logs epics-tunnel 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || true)
    if [[ -n "$url" ]]; then printf '%s\n' "$url" | tee "$APP/public-url"; exit 0; fi
    sleep 1
done
echo "no URL yet; inspect docker logs epics-tunnel locally" >&2
exit 1
