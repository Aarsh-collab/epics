# ASU EPICS — eNABLE Prosthetics Software

## What this project is

AI-assisted full-limb mesh generation to give eNABLE device designers a scaled reference
mesh they can bring into Rhino to design a padded prosthetic device. This is **not** a
medical-grade measurement system — the mesh is reference geometry for device design,
padding/clearance is intentional, and a human review gate sits before anything is printed
or acted on.

Full original planning doc: `project_docs/enable_prosthetics_next_steps.docx`
(rendered pages: `project_docs/rendered/`). `project_docs/` is gitignored and exists only
on the original laptop.

### The central technical risk

Whether an image-to-3D model preserves the *observed* limb geometry — including residual
or congenital limb differences — without inventing a missing hand, wrist, digit, or extra
anatomy that isn't there. This matters more than visual polish or texture quality.

## Model choice: TRELLIS.2

The code runs **Microsoft TRELLIS.2** (`microsoft/TRELLIS.2-4B`, https://github.com/microsoft/TRELLIS.2),
pinned commit `75fbf0183001ed9876c8dbb35de6b68552ee08bd` in `sol/setup_env.sh`. Defaults: seed 42,
resolution 1024, remesh on. It needs the gated Hugging Face models `facebook/dinov3-vitl16-pretrain-lvd1689m`
and `briaai/RMBG-2.0`.

Earlier informal tests (original TRELLIS, compared against Hunyuan3D-2 and the hosted Meshy):

- Full 5-finger hand, single clear dorsal photo: good — correct finger count and thumb
  separation; minor grey/purple discoloration near the wrist (frame edge).
- Full hand, 4-image multi-image conditioning: **bad** — 6-7 fused digits, no thumb.
  Suspected input inconsistency, not confirmed.
- Residual/congenital limb-difference hand, single photo: **most important result** — kept
  the single thumb-like digit and did NOT hallucinate missing fingers. Digit a bit bulbous,
  wrist taper a little abrupt.

Known TRELLIS.2 behaviour on forearm photos (see `docs/testing_bugs.md`):

- When the limb runs off the photo edge, it invents geometry there (e.g. an elbow branch or a
  hand-like end). This is why photo preparation (cut at Start) exists.
- It often returns a **double wall** (outer skin + slightly smaller inner wall facing inward)
  plus small hidden fragments. This is why the outer-shell cleanup exists.
- The single view leaves the hidden side unconstrained (seed 7 gave D-shaped cross-sections).

Do not fine-tune/post-train until baseline failures are measured on an approved evaluation set.
No verified limb-mesh training data exists yet.

## User preferences

- Hand measurements are hard for people to take reliably. Use photo landmarks/markers to
  locate and cut the limb; measurements are for scaling and as a cross-check, not for
  positioning the cut.
- A two-colour "paint keep/discard zones" approach (`zones.py`) was tried and **removed by
  the team on 2026-10-27** because it didn't work as intended. Do not restore it unless asked.
- Plain-language explanations and replies; keep changes scoped to what was asked.

## Repository layout (built so far)

Python package `src/epics_geometry/` (install with `uv sync`; tests `uv run --with pytest pytest -q`):

| Module | Does |
|---|---|
| `trellis_run.py` | `epics-generate`: photo → GLB with TRELLIS.2. `generate_glb(..., model_input_path=None)` runs `pipeline.preprocess_image` itself and then `run(preprocess_image=False)`, saving the exact model input; RGBA input skips TRELLIS background removal. Run record has `input_has_alpha`, `model_input`, commit, seed, settings. |
| `photo_prep.py` | Photo preparation before TRELLIS: cut the photo at the Start landmark so the model can't invent what lies past it. See below. |
| `outer_shell.py` | `epics-outer-shell`: experimental mesh cleanup — keep the outer wall, drop hidden/detached pieces, repair small defects. See below. |
| `stub.py`, `stub_geometry.py`, `measurements.py` | `epics-scale-stub` / `epics-measure-stub`: auto-align a stub (flat base + rounded tip), match 7 measurements (length; circumference + max width at 25/50/75%). Statuses include `measurements_matched`, `alignment_ambiguous`. Details: `docs/stub_scaling.md`. |
| `scale.py` | `epics-scale`: length-only uniform scaling. |
| `validate.py` | `epics-validate`: independent QA of length/circumferences. Its "not watertight" WARN counts UV-seam splits (position-merged meshes are watertight) — known quirk. |
| `pipeline.py` | `epics-pipeline`: generate → (outer_shell) → scale → validate, writes `manifest.json` (input hash, model/seed/settings, stage statuses, `review_status: pending_human_review`). Statuses: `complete`, `cleanup_refused`, `alignment_ambiguous`, `validation_failed`, `error`… If the capture has `"preparation"`, uses `capture_dir/prep/prepared.png` after checking its sha256. |
| `server.py` | `epics-server`: FastAPI service, passcode auth, one GPU worker, job queue. |
| `glb_io.py`, `synthetic.py`, `errors.py` | GLB loading, synthetic test meshes, shared errors. |

Other folders:

- `web/`: React + Vite capture app (`App.tsx`, `capture.ts`, `components/ModelPreview.tsx`). The flow is: photos, then landmarks (Start/Tip), then 7 measurements, then Review, then Generate. Tests: `npm test`. Build: `npm run build`.
- `sol/`: the ASU Sol cluster path (`setup_env.sh`, `run_pipeline.sbatch`, `register_kernel.sh`). Also `notebooks/sol_pipeline.ipynb` for Open OnDemand.
- `deploy/spark/`: the Spark service (`Dockerfile`, `up.sh`, `deploy.sh`, `tunnel.sh`, `README.md`).
- `docs/stub_scaling.md`: the scaler's method and validation log.
- `docs/testing_bugs.md`: the bug log TB-001 to TB-004. Add new findings there, keeping the observed behaviour separate from the suspected cause.
- `schemas/`: JSON schema for the measurements. `examples/` holds `measurements.example.json`.
- `output/` (gitignored): local inspection renders and GLBs made from test photos. Never commit it.

Last full test run: 114 Python tests and 4 web tests passing, with a clean web build.

### Photo preparation (`photo_prep.py`), on by default in the web app

- `PrepSettings(cap="straight"|"oval", cap_depth=0.08, pad=0.05, blend=0.25)`.
- `prepare(photo_bytes, start, tip, mask_fn, settings)` returns `Prepared(rgba, mask, face, record)`. `preview()` renders it on grey, with an optional face outline.
- The mask comes from BiRefNet `briaai/RMBG-2.0` (`trellis_mask`, loaded lazily on CUDA).
- Steps:
  - Apply the EXIF orientation.
  - Find the limb's crossing at Start and keep the limb component on the Tip side.
  - Limit antialiasing to a 1 px edge band.
  - Add a transparent pad.
- It refuses with a message when the landmarks are too close, Start is off the limb, Tip is on another object, or another large object remains.
- It warns when the limb touches the photo edge on the Tip side.
- The `oval` cap mirrors nearby skin into a shallow, lighter end face. It is **experimental**; `straight` is the recommended default (TB-004).
- Server endpoints:
  - `POST /api/preparations` (photo, landmarks JSON, cap)
  - `GET /api/preparations/{id}/files/{name}`
  - `POST /api/preparations/{id}/discard`
- On job submission, a `preparation` field moves `data/preps/<id>` into the job's `input/prep`. The server returns 409 if the photo sha or landmarks no longer match.
- The UI shows the original and the prepared preview side by side. Generate stays disabled until the current preview is approved, and changing the photo, markers or cap invalidates it.

### Outer-shell cleanup (`outer_shell.py`), opt-in checkbox (default off)

- `extract(source, target, review=None)` returns a report. It raises `ShellError(message, report)` on refusal, and the original GLB is kept.
- Pieces:
  - The largest piece is the outer wall.
  - A second large piece is removed as the inner wall only if it passes all three checks: at least 99% of its points are inside, at most 1% are visible from 26 outside directions, and its area/volume ratio is in (0.8, 1.0).
  - Smaller pieces are removed when hidden inside. When they touch or cross the outer wall and are visible, they are fused: welded or nudged into contact, then unioned with manifold3d, carrying UVs through.
  - Pieces separated by more than 0.01% of the bounding-box diagonal are removed and flagged.
- Repairs: back-to-back fins are removed and small holes filled (≤32 edges and ≤10% perimeter). A hole that crosses a UV seam gets a flat colour.
- Checks: shape and end shift (≤2% length change), texture colour, and an export re-check.
- Outputs: `outer_shell_report.json` and `outer_shell_review.glb`. The review GLB shows the kept surface in see-through grey, removed pieces in red and patches in yellow.
- The server adds `_cleanup_summary` to the job result, and the web app shows a `CleanupReport` table.
- Checked on a real nine-piece arm GLB: one closed body with the texture and tip unchanged. Scaling matched fake measurements.
- **Limitation:** it separates only *disconnected* walls. Inner and outer walls joined at a cut rim (the normal result from prepared photos) are refused as a "bow-tie" hole or non-manifold.
- Whether to turn it on by default is a team decision that is still pending.

## DGX Spark service (live)

- **Machine:** reach it with `ssh compute` (the SSH config alias, user `hithesh`, over Tailscale).
  - It is a **shared lab machine**. The lab granted full access on 2026-09-25, but never touch containers, images or files this project didn't create.
  - Other people's work (dgx-control-center, open-webui, TensorRT-LLM, etc.) runs under the same home directory.
  - Hardware: GB10 Grace Blackwell, aarch64, 128 GB unified memory, CUDA 13.0.
  - `~/.claude/settings.json` on the Mac asks before running `ssh compute` commands.
- **Project files** (all under `~/epics-app/`):
  - `repo/`: synced by `deploy.sh` and mounted read-only.
  - `env`: holds `EPICS_PASSCODE`, mode 600. **Never print or commit it.**
  - `hf-token`: an HF token with DINOv3 and RMBG access. Generation works.
  - `hf-cache/`, `bin/cloudflared`.
  - `data/jobs/<id>` and `data/preps/<id>`: deleted when the user leaves the result page, or 1 h after finishing.
  - `test-inputs/`: `arm_clean.png`, plus `arm_band_*` from the abandoned marker experiment.
  - `experiments/prep-compare/arm_clean/`: the TB-004 outputs.
  - `public-url`: the current tunnel URL.
- **Containers:**
  - `epics-app` runs the project image `epics-app:repair` on `127.0.0.1:8090`. The image is built from `deploy/spark/Dockerfile`, which is the shared `trellis2:flashattn-render` plus `manifold3d==3.5.4`. `up.sh` rebuilds it only when the Dockerfile's sha changes (label `epics.dockerfile`). The shared base image is never modified.
  - `epics-tunnel` is a Cloudflare quick tunnel. Its URL changes on every restart, so check it with `ssh compute 'cat ~/epics-app/public-url'`. TLS ends at Cloudflare, so use **test photos only**.
- **Deploy:** run `bash deploy/spark/deploy.sh` from the repo root. It builds `web/`, rsyncs the tracked and untracked-but-not-ignored files, and runs `up.sh`.
  - It restarts the container, so first check that no job is running: `ssh compute 'ls ~/epics-app/data/jobs'`.
  - Health check: `ssh compute 'curl -s 127.0.0.1:8090/api/session'` should return `{"authenticated":false}`.
- **Local dev:** run `EPICS_PASSCODE=dev EPICS_COOKIE_SECURE=0 uv run --extra server epics-server` (port 8000), and `cd web && npm run dev`, which proxies `/api`. Without CUDA, jobs stop at generate.
- **Hunyuan3D-2:** an earlier build on the Spark was deleted. The local `Hunyuan3D-2/` checkout is for reference only.

## Git

- Remote: `origin https://github.com/Aarsh-collab/epics.git`. **The repository is public.** Never commit secrets, passcodes, tunnel URLs, participant or test photos, or generated meshes (`*.glb` is gitignored).
- `main` is the shared branch. Work happened on `integrate-sol` and was fast-forwarded into `main`.

## Open work (next steps first)

1. **Split inner and outer walls that join at the cut rim.** Prepared photos give a clean tube, but TRELLIS.2 fuses its inner wall to the outer one at the Start rim, so cleanup refuses it and nothing reaches scaling yet. This is the next mesh step.
2. **Get clearer limb photos** for the prepared-vs-original comparison. Only `arm_clean.png` exists so far.
3. **TB-002:** the generated arm has no clear flat base, so the scaler returns `alignment_ambiguous`. Photo preparation is meant to fix this once item 1 is solved.
4. **TB-003:** the side photo is required and uploaded, but unused. Decide whether to make it optional or use it.
5. **Outer-shell cleanup default-on?** This is the team's decision.
6. **Validator watertight check:** merge positions before testing, so UV seams don't trigger a WARN.
7. **Input image quality checks:** blur, framing, visibility and lighting, as a layer before TRELLIS.
8. **Multi-image / multi-angle conditioning:** this is untested on TRELLIS.2, and the earlier multi-image result was the worst so far.
9. **Calibration card / scale pipeline:** use ArUco/AprilTag or a 100 mm ruler with OpenCV. It must be validated against TRELLIS, because the model may distort the card.
10. **Formal evaluation scorecard:** for each run, log the input set, model, seed and settings, the mesh, and the designer's verdict: usable, usable with cleanup, or unusable, with the reason.
11. **Rhino handoff test:** no mesh has been imported into Rhino or used for a padded device yet. Do this before choosing between GLB, OBJ and STL.
12. **Hunyuan3D side-by-side:** this would have to be rebuilt from scratch.

## Guardrails (from planning doc — still binding)

- No post-training/fine-tuning until baseline failures are measured on an approved evaluation set.
- Never call a mesh medically or metrically accurate just because it imports into Rhino,
  matches measurements, or looks plausible in preview — it's reviewed reference geometry only.
- No sending participant/sensitive imagery to any hosted service until consent, storage, and
  institutional requirements are confirmed (this includes the Cloudflare tunnel).
- No deploying a copied external application — rebuild the workflow with approved
  dependencies, licenses, auth, storage, ownership.
- Keep participant/test photos and generated meshes out of the repository unless the team
  explicitly approves.

## Reference links

- Microsoft TRELLIS.2: https://github.com/microsoft/TRELLIS.2 (original TRELLIS: https://github.com/microsoft/TRELLIS)
- Tencent Hunyuan3D: https://github.com/Tencent-Hunyuan/Hunyuan3D
- Meshy workspace: https://www.meshy.ai/workspace
- StemDome generator: https://stemdomez.com/stemdome-generator
