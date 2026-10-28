#!/bin/bash
# Start (or restart) the EPICS web service on the DGX Spark. Run on the Spark:
#   bash ~/epics-app/repo/deploy/spark/up.sh
# Runs epics-app:repair (deploy/spark/Dockerfile: the shared trellis2:flashattn-render image plus
# manifold3d, built here when the Dockerfile changes) with this repo and ~/TRELLIS.2 mounted read-only. Only binds 127.0.0.1:8090; Tailscale Funnel provides the public HTTPS.
set -euo pipefail
APP=${EPICS_APP_DIR:-$HOME/epics-app}
IMAGE=${EPICS_IMAGE:-epics-app:repair}
PORT=${EPICS_PORT:-8090}
# Hard cap so a runaway job is OOM-killed inside the container (which then restarts) instead of
# exhausting the shared Spark's 128 GB; on 2026-10-21 an uncapped validation run froze the machine.
MEMORY=${EPICS_MEMORY:-64g}

if [ ! -f "$APP/env" ]; then
    echo "create $APP/env (chmod 600) containing EPICS_PASSCODE=<passcode>" >&2
    exit 2
fi
mkdir -p "$APP/data" "$APP/hf-cache"
if [ "$IMAGE" = "epics-app:repair" ]; then
    # Rebuild only when the Dockerfile changed; the label records which version an image came from.
    DOCKERFILE="$APP/repo/deploy/spark/Dockerfile"
    WANT=$(sha256sum "$DOCKERFILE" | cut -c1-16)
    HAVE=$(docker image inspect -f '{{ index .Config.Labels "epics.dockerfile" }}' "$IMAGE" 2>/dev/null || true)
    if [ "$WANT" != "$HAVE" ]; then
        docker build -t "$IMAGE" --label "epics.dockerfile=$WANT" -f "$DOCKERFILE" "$APP/repo/deploy/spark"
    fi
fi
# A token with access to the gated DINOv3 and RMBG-2.0 repos; falls back to the account's own token.
HF_TOKEN_FILE="$APP/hf-token"
[ -f "$HF_TOKEN_FILE" ] || HF_TOKEN_FILE="$HOME/.cache/huggingface/token"
docker rm -f epics-app >/dev/null 2>&1 || true
docker run -d --name epics-app --restart unless-stopped --gpus all \
    --memory "$MEMORY" --memory-swap "$MEMORY" \
    --user "$(id -u):$(id -g)" -e HOME=/tmp \
    --env-file "$APP/env" \
    -e HF_HOME=/hf -e EPICS_DATA_DIR=/data -e EPICS_WEB_DIST=/workspace/epics/web/dist \
    -e PYTHONPATH=/workspace/epics/src:/workspace/TRELLIS.2 \
    -v "$HOME/TRELLIS.2":/workspace/TRELLIS.2:ro \
    -v "$APP/repo":/workspace/epics:ro \
    -v "$APP/hf-cache":/hf \
    -v "$HF_TOKEN_FILE":/hf/token:ro \
    -v "$APP/data":/data \
    -w /workspace/TRELLIS.2 \
    -p "127.0.0.1:$PORT:8000" \
    --entrypoint /opt/venvs/trellis2/bin/python \
    "$IMAGE" -m epics_geometry.server
echo "epics-app listening on 127.0.0.1:$PORT (logs: docker logs -f epics-app)"
