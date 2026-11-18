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

## Public URL: Cloudflare quick tunnel

The service binds only `127.0.0.1:8090`; `tunnel.sh` runs container `epics-tunnel` (the checksum-verified
`~/epics-app/bin/cloudflared`, restart `unless-stopped`) and writes the current URL to `~/epics-app/public-url`.

```bash
ssh compute 'cat ~/epics-app/public-url'                       # current public URL
ssh compute 'bash ~/epics-app/repo/deploy/spark/tunnel.sh'     # restart the tunnel (new URL)
```

Quick tunnels have no uptime guarantee, and any tunnel restart (crash, Docker restart, reboot) gets a new
`*.trycloudflare.com` URL, so re-check `public-url` if the old link stops working. TLS ends at Cloudflare,
so uploaded photos are visible to it in transit: use test photos only, not participant images.

The Spark's Tailscale network belongs to the lab and is not ours to publish through, so Tailscale Funnel is
not used. The planned stable URL is a Cloudflare named tunnel on our own domain, set up on Jetstream; see
[stable-url-options.md](../stable-url-options.md).

### Fake generation (local development only)

Set EPICS_FAKE_GENERATE to an absolute path to a local test GLB to run the CPU pipeline without TRELLIS. The result and manifest are marked as fake; the GLB is unrelated to the uploaded photo. Leave this variable unset on the Spark and production deployments. Photo preparation and side checks still require their own mask model or test stub. Never commit test meshes.
