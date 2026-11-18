#!/bin/bash
# Public HTTPS for epics-app through a Cloudflare quick tunnel (no account, no sudo). Run on the Spark:
#   bash ~/epics-app/repo/deploy/spark/tunnel.sh
# The container restarts with Docker, but every restart gets a new *.trycloudflare.com URL;
# the current one is written to ~/epics-app/public-url. TLS ends at Cloudflare, so test photos only.
set -euo pipefail
APP=${EPICS_APP_DIR:-$HOME/epics-app}
PORT=${EPICS_PORT:-8090}

docker rm -f epics-tunnel >/dev/null 2>&1 || true
# python:3.12-slim only supplies CA certificates for the verified cloudflared binary in $APP/bin.
docker run -d --name epics-tunnel --restart unless-stopped --network host \
    --user "$(id -u):$(id -g)" -v "$APP/bin":/opt/cloudflared:ro \
    python:3.12-slim /opt/cloudflared/cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >/dev/null
for _ in $(seq 60); do
    url=$(docker logs epics-tunnel 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || true)
    if [ -n "$url" ]; then echo "$url" | tee "$APP/public-url"; exit 0; fi
    sleep 1
done
echo "no URL yet; see: docker logs epics-tunnel" >&2
exit 1
