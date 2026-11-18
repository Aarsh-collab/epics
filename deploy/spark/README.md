# DGX Spark deployment

Passcode-protected EPICS service on the lab DGX Spark (`ssh compute`, user `hithesh`). It is a shared
lab machine: everything this deployment creates lives in `~/epics-app/` and the `epics-app` container.

| Path / name | What |
|---|---|
| `~/epics-app/repo/` | tracked repo files + `web/dist`, synced by `deploy.sh` (mounted read-only) |
| `~/epics-app/env` | `EPICS_PASSCODE=...`, mode 600, never committed |
| `~/epics-app/hf-token` | optional HF token with DINOv3 + RMBG-2.0 access (else `~/.cache/huggingface/token`) |
| `~/epics-app/data/jobs/<id>/` | `input/` (capture + photos), `out/` (pipeline output), `job.json`; deleted when the user leaves the result page, else 1 hour after finishing |
| `~/epics-app/hf-cache/` | Hugging Face cache for TRELLIS.2-4B and its conditioning/background models |
| image `epics-app:repair` | `Dockerfile`: the shared `trellis2:flashattn-render` plus `manifold3d` for the outer-wall cleanup; built by `up.sh` when the Dockerfile changes (the base image is not modified) |
| container `epics-app` | `epics-app:repair` running `python -m epics_geometry.server` on `127.0.0.1:8090` |

```bash
bash deploy/spark/deploy.sh                                   # Mac: build, sync, restart the container
ssh compute 'docker logs --tail 50 epics-app'
ssh compute 'curl -s 127.0.0.1:8090/api/session'               # {"authenticated":false} when healthy
```

To change the passcode, edit `~/epics-app/env` and re-run `up.sh`; existing sessions stop working.

## Gated models

TRELLIS.2 loads `facebook/dinov3-vitl16-pretrain-lvd1689m` and `briaai/RMBG-2.0`, both gated on Hugging
Face. The `hithesh` account token is not authorized for DINOv3 (403); an authorized read token is saved as
`~/epics-app/hf-token` (mode 600), and jobs generate. To replace it, request access on both model pages,
create a read token, save it there, and re-run `up.sh`.

## Public URL: Tailscale Funnel with quick-tunnel fallback

**Decision for issue #18:** use Funnel to avoid buying a domain and to reduce
setup work, subject to the machine/tailnet owner's approval and the applicable
Tailscale plan. See [comparison and rationale](../stable-url-options.md).
Funnel provides a public HTTPS URL in the node's tailnet DNS domain. It does not
support using a DuckDNS/custom hostname directly.

The Spark is shared. Its tailnet belongs to the lab; **approval is not yet
confirmed**. Do not enroll it in another tailnet, change lab policy, restart its
Tailscale daemon, or run a global Serve/Funnel reset. These scripts only attempt
one EPICS route and refuse if existing sharing is not exactly the route recorded
by this project. They never install Tailscale, log in, or grant permissions.

Use **test photos only** until consent and storage approvals exist. Funnel
publishes the route to the internet; visitors do not need Tailscale. The app's
passcode gate remains necessary. TLS terminates at the host's Tailscale daemon.

### Prerequisites and approval

Before deployment, an authorized administrator must:

1. Approve public access to the EPICS local app and confirm that the tailnet's
   plan permits this project. Funnel availability does not mean every plan is
   suitable for institutional use.
2. Restore authorized server access. Install/update Tailscale if needed (CLI
   1.52+), and ensure the host is connected to the approved tailnet.
3. Enable MagicDNS, HTTPS certificates and the required Funnel node permission.
   Grant the deployment user sufficient local CLI permissions. The scripts do
   not use sudo, modify operator permissions or change the tailnet policy.
4. Reserve a Funnel HTTPS port for EPICS: 443 by default, or 8443/10000 if
   explicitly approved. Our conservative guard refuses ANY unrelated existing
   Serve/Funnel configuration, including routes on other ports. If sharing is
   already in use, ask the lab admin to integrate EPICS separately; do not reset
   that configuration or bypass the guard.
5. Ensure Python 3.10+, Docker, the local EPICS app and its passcode are available.
   Default backend: HTTP on `127.0.0.1:8090`. The helper checks
   `/api/session` returns `{"authenticated":false}` before exposure.

The CLI can request HTTPS/Funnel authorization if it is not already enabled;
an administrator should complete the prerequisites interactively first. Our
commands time out rather than silently starting a different public endpoint.
Coordinate with other operators so sharing configuration is not edited while
setup/rollback runs.

### Local configuration and credentials

All configuration below lives in `~/epics-app/`, outside the checkout:

- `tunnel-mode`: plain text `funnel` or `quick`; absent means quick.
- `funnel-approved`: plain text `yes`, created only after owner approval.
  This records the operator's acknowledgement; it does not grant permissions.
- `funnel-state.json`: generated ownership/recovery record, mode 600. Do not
  edit it manually. It records the DNS name, HTTPS port and backend for scoped
  restart/rollback checks.
- `public-url`: generated configured URL. Do not commit or post it in the repo.
- The existing `env` passcode file stays mode 600.

There is **no Cloudflare tunnel token** in Funnel mode. Tailscale login identity,
node keys and certificate state are managed by the installed Tailscale service,
outside this repo. Do not copy its state, auth keys or credentials into Git,
chat or issue comments. If an administrator uses an enrollment key for a new
team-controlled VM, they must handle it privately; these scripts do not accept
or store enrollment keys.

Old Cloudflare named-tunnel token files do not select named mode anymore.
The unmerged named-tunnel candidate was replaced by this implementation.

### Start Funnel (on the authorized server)

After approval and syncing this branch's scripts:

```bash
mkdir -p ~/epics-app
# Only after the owner has approved Funnel:
(umask 077; printf 'yes\n' > ~/epics-app/funnel-approved)
printf 'funnel\n' > ~/epics-app/tunnel-mode
bash ~/epics-app/repo/deploy/spark/tunnel.sh
cat ~/epics-app/public-url
```

`tunnel.sh --funnel` selects Funnel for one invocation; the approval record is
still required. `tunnel-funnel.sh start` is the direct entry point.
Use `EPICS_APP_DIR` for a different project directory, `EPICS_PORT` for a
different backend, and `EPICS_FUNNEL_HTTPS_PORT` for an approved alternative
HTTPS port. Supply the same settings on subsequent starts. To change an active
route's settings, turn off the old route first using its saved ownership record.

The helper reads the node DNS name from local Tailscale status, enables a
background HTTPS proxy, then verifies the exact route and public Funnel flag.
Only then does it publish `public-url` and remove the project's `epics-tunnel`
quick-tunnel container. It does not restart `epics-app` or alter other containers.
Unknown sharing configuration blocks setup even if its port differs.

A failure never silently falls back to a random quick URL. Failures after the
activation attempt retain `funnel-state.json` for scoped recovery and clear
`public-url`; inspect locally before retrying or running rollback.

`--bg` persists Funnel sharing across host/Tailscale restarts. Keep the node and
tailnet DNS names stable and preserve the Tailscale service's state. Deleting,
renaming or re-enrolling the node can change its address. The URL is unrelated
to the lifetime of the app container. `up.sh`/`deploy.sh` manage the application
only; run `tunnel.sh` separately for exposure configuration.

### Switch back to the quick tunnel

```bash
bash ~/epics-app/repo/deploy/spark/tunnel.sh --quick
cat ~/epics-app/public-url
# Persist that choice for future plain tunnel.sh invocations:
printf 'quick\n' > ~/epics-app/tunnel-mode
```

Rollback first verifies the recorded EPICS route and disables it with a
port-specific `tailscale funnel ... off` command. It never uses `reset`.
If configuration has changed or another route exists, it stops for an operator
to inspect rather than risk another service. If no EPICS ownership record
exists, quick mode does not change Tailscale.

To stop only the owned Funnel route:
`bash ~/epics-app/repo/deploy/spark/tunnel-funnel.sh off`.

Quick mode retains the existing verified `~/epics-app/bin/cloudflared` binary,
`python:3.12-slim` container, loopback origin and restart policy. It needs no
Cloudflare account or token. Quick URLs change when the tunnel restarts; check
`public-url` again. HTTPS terminates at Cloudflare in quick mode.

### Verify the live setup

Configuration verification is not proof of external DNS/TLS, access policy or
upload performance. From a separate browser/client outside the tailnet:

1. Open the saved URL. Check HTTPS is valid, the passcode gate works, and an
   unauthenticated `/api/session` returns `{"authenticated":false}`.
2. Check the full flow with a test photo, including mesh/report downloads.
   Funnel has non-configurable bandwidth limits; record transfer performance.
3. When no EPICS job is running, restart **only `epics-app`** twice. After each
   restart, wait for local health and confirm that the original external URL
   still works. Funnel runs in the host daemon, not an `epics-tunnel` container.

```bash
before=$(cat ~/epics-app/public-url)
docker restart epics-app
# Wait for local /api/session health, then check from the external client:
# curl --fail --silent --show-error "$before/api/session"
test "$(cat ~/epics-app/public-url)" = "$before"
# Repeat the restart and external check a second time.
```

Do not treat the unchanged file as availability proof. Record successful
external checks after both restarts in the PR. Reboot testing, if desired, is
for the lab administrator to schedule; never restart a shared host/daemon just
to exercise this script.

**Pending:** lab approval/access and all real-server checks above. Offline tests
do not validate real policy, certificates, transfers or restart persistence.

### Offline checks and troubleshooting

```bash
uv run --with pytest pytest tests/test_funnel_deploy.py -q
bash deploy/spark/test-tunnel.sh
```

These mock Tailscale, Docker and the health endpoint. For live errors, inspect
`tailscale status`, `tailscale serve status --json`, `tailscale funnel status`
and the local app health privately. Do not post raw host/account details.
A stale/mismatched ownership record requires admin inspection; do not delete it
to bypass the guard. Concurrent admin changes or partial CLI failure can need
manual recovery. A successful Funnel setup with an unavailable Docker daemon
may require the operator to retire the old quick-tunnel container separately.

### Jetstream

The same scripts work on an authorized team-controlled Linux VM running the app
on loopback. Its owner must approve the Tailscale account/plan and preserve
daemon identity/state. Do not migrate or reuse the lab Spark's Tailscale state.
Use a new VM identity; moving to a different node generally changes the Funnel
hostname. Coordinate app deployment with #16. Caddy remains an alternative if
Jetstream provides public networking and suitable DNS; no Caddyfile is added
because it is not the chosen option here.

References: [Funnel requirements](https://tailscale.com/docs/features/tailscale-funnel)
and [CLI, background persistence and scoped off](https://tailscale.com/docs/reference/tailscale-cli/funnel).

### Fake generation (local development only)

Set EPICS_FAKE_GENERATE to an absolute path to a local test GLB to run the CPU pipeline without TRELLIS. The result and manifest are marked as fake; the GLB is unrelated to the uploaded photo. Leave this variable unset on the Spark and production deployments. Photo preparation and side checks still require their own mask model or test stub. Never commit test meshes.
