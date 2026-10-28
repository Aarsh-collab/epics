# EPICS prosthetics geometry project

Exploratory workflow for turning a single image into a 3D reference mesh with [TRELLIS.2](https://github.com/microsoft/TRELLIS.2), then checking and adjusting its dimensions against physical measurements.

Current stage: evaluating generated meshes and a measurement-based scaling workflow. A plausible mesh is **not** a verified anatomical scan or a ready-to-fit prosthetic surface. Any use involving a person requires appropriate review and validation.

The upstream TRELLIS.2 checkout, participant/example photos, local planning documents, and machine-specific notes are intentionally not included in this repository.

## End-to-end workflow (capture → Sol → reviewed mesh)

| Stage | Where | Command |
|---|---|---|
| Capture photos, landmarks, 7 measurements | laptop browser | `cd web && npm install && npm run dev` → download `capture.json` |
| Generate with TRELLIS.2, scale, validate | Sol GPU | `sbatch sol/run_pipeline.sbatch <capture_dir> <out_dir>` or `notebooks/sol_pipeline.ipynb` in Open OnDemand |
| Human review, Rhino import | laptop | `scaled.glb` (metres) + `manifest.json` from `<out_dir>` |

**One-time Sol setup** (on a GPU node: an OOD session terminal, or `salloc -p general -q public -G a100:1 -c 8 --mem=64G -t 3:00:00`):

```bash
git clone https://github.com/Aarsh-collab/epics.git && cd epics
bash sol/setup_env.sh        # conda env + TRELLIS.2 (pinned commit) + CUDA extensions on /scratch/$USER/epics
bash sol/register_kernel.sh  # optional: "EPICS TRELLIS.2" kernel for OOD Jupyter
```

`sol/env.sh` holds the paths and module loads; if the CUDA module isn't found, run `module avail cuda` and set
`EPICS_CUDA_MODULE`. If `sbatch` rejects the partition/QoS, edit the `#SBATCH` lines in `sol/run_pipeline.sbatch`.

**Each capture:** copy `capture.json` and both original photos into one folder on Sol (OOD file manager or `scp`), then

```bash
sbatch sol/run_pipeline.sbatch /scratch/$USER/captures/p01 /scratch/$USER/runs/p01
# extra epics-pipeline flags go after the two paths, e.g. --no-remesh, --seed 7, --outer-shell
```

`<out_dir>` gets `generated.glb`, `scale_report.json`, `validation.json`, `scaled.glb` (only when the scaler matched all
seven values) and `manifest.json` (input hash, model/seed/settings, GPU, Slurm job, TRELLIS.2 commit, stage statuses,
`review_status: pending_human_review`). A run that stops early still writes the manifest saying where and why.

Commands available after `uv sync` (laptop) or `setup_env.sh` (Sol):

| Command | Needs GPU | Does |
|---|---|---|
| `epics-pipeline` | for generation only (`--glb` skips it) | all stages below, plus the manifest |
| `epics-generate` | yes | TRELLIS.2 photo → GLB |
| `epics-scale-stub` / `epics-measure-stub` | no | scale to 7 measurements / re-measure (see below) |
| `epics-validate` | no | independent QA: length + circumferences vs. a GLB (`--axis-mode canonical` for scaler output) |
| `epics-outer-shell` | no | experimental: keep the outer wall of a nested double shell |
| `epics-scale` | no | length-only uniform scaling |

## Hosted service on the DGX Spark

The same pipeline also runs as a passcode-protected web service on the lab's DGX Spark: the capture app
uploads `capture.json` and both photos, one GPU worker runs `epics-pipeline` jobs in order, and the page
offers `scaled.glb`, `manifest.json` and the reports for download. Code: `src/epics_geometry/server.py`;
deployment: `deploy/spark/`.

```bash
bash deploy/spark/deploy.sh   # from this Mac: build web/, sync tracked files to ~/epics-app/repo, restart
```

On the Spark, `~/epics-app/` holds `env` (`EPICS_PASSCODE=...`, mode 600, never committed), `data/jobs/`
(uploads and results, deleted when the user leaves the result page, or 1 hour after a job finishes if that signal never arrives), and `hf-cache/` (TRELLIS.2, DINOv3 and RMBG weights). The
container `epics-app` reuses the `trellis2:flashattn-render` image, runs as `hithesh`, and binds only
`127.0.0.1:8090`; a tunnel provides the public HTTPS URL (see `deploy/spark/README.md`).

Local development: `EPICS_PASSCODE=dev EPICS_COOKIE_SECURE=0 uv run --extra server epics-server` (port 8000)
and `cd web && npm run dev`, which proxies `/api` to it. Without CUDA, jobs stop at the generate stage.

## First scaling experiment

This first stage sets **overall length only**. It applies one uniform scale factor
to the complete GLB, preserving its proportions and materials where the GLB
loader/exporter supports them. It does not correct circumferences, local shape,
or inferred anatomy. The operator must identify the mesh's length axis and which
end is proximal; an axis-aligned bound is not automatically an anatomical landmark.

```bash
uv sync
uv run epics-scale input.glb output_scaled.glb \
  --length-mm 76.2 --axis x --proximal-end min
uv run --with pytest pytest -q
```

The output GLB's coordinates are in **metres**, as required by glTF. A JSON file
beside it records dimensions in **millimetres**, the scale factor, and limitations.
The command reloads the exported GLB and checks its length rather than trusting
the in-memory result. Start with a known-size non-human test object; an accurate
bounding length alone does not establish an accurate or safe body-contact shape.

## Stub scaling (length + three cross-sections)

For isolated, roughly straight stubs with a flat base cutoff and no fingers,
`epics-scale-stub` aligns the mesh automatically and matches seven
measurements: length, plus circumference and max width at 25/50/75% of it.
Run `epics-measure-stub` to re-measure any exported GLB independently.

```bash
uv run epics-scale-stub input.glb --measurements examples/measurements.example.json --output scaled.glb
uv run epics-measure-stub scaled.glb
```

See [docs/stub_scaling.md](docs/stub_scaling.md) for capture requirements,
measurement definitions, the method, the report statuses, and the validation
log, including the first real TRELLIS.2 result.
