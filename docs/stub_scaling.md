# Stub scaling

`epics-scale-stub` takes a TRELLIS.2 GLB of an isolated limb stub plus seven
human measurements, aligns the stub automatically, matches its length and
three cross-sections, and writes a scaled GLB with a measurement report. It
runs on CPU (about 2 s for a 500k-face TRELLIS.2 mesh on a laptop). Spark and
Sol are only needed for TRELLIS generation.

Matching the entered values is a **software check**, not a clinical accuracy
claim. The output is reference geometry for device design and needs human
review. Everything between and beyond the measured stations is the generator's
inferred shape.

## Supported input (version 1)

- One isolated, roughly straight stub with **no fingers or other branches**.
- One recognizable **flat base cutoff** (a closed flat cap or a flat open edge)
  at one end, and a rounded, non-flat tip at the other.
- A static GLB: triangles only, no animation, skins, morph targets, Draco
  compression, or required extensions other than WebP textures.
- A single connected surface. Material and UV seams are fine. Background
  surfaces are rejected. Specks under 0.2% of the surface area are kept and
  listed in the warnings.
- A solid outer surface. Hollow, double-walled shells are rejected (see the
  validation log below).
- Cross-sections that are not strongly concave. A taut tape bridges dents, so
  a section whose contour is more than 1% longer than its convex outline is
  rejected.

If the photo includes arm beyond the base the user chose, recapture or crop
upstream and regenerate. The scaler never guesses an internal anatomical cutoff.

## Measurements (all millimetres)

| Measurement | How to take it |
|---|---|
| Overall length | Straight distance along the stub's length direction from the selected base to the furthest tip. Not a tape following the curved skin. |
| Circumference at 25/50/75% | Tape loop perpendicular to the length direction at that distance from the base. |
| Max width at 25/50/75% | Largest straight-across span of the same cross-section, using a caliper or two parallel straightedges and a ruler. Not a curved surface measurement. |

Station distances are `fraction × length`, so a 120 mm stub is measured at
30, 60 and 90 mm. In code: `epics_geometry.measurements.station_distances_mm(120)`.
Photo overlays can show *where* these stations are, but perspective means a
pixel percentage cannot establish their physical position. The base chosen in
the photo must be the same base cutoff that appears in the mesh.

Input file: [`examples/measurements.example.json`](../examples/measurements.example.json),
schema: [`schemas/stub_measurements.v1.schema.json`](../schemas/stub_measurements.v1.schema.json).
All three sections are required. The scaler also rejects physically
impossible pairs: for any closed convex section, `2 × width < circumference ≤ π × width`.

### Team handoff

| Who | Responsibility |
|---|---|
| Rohan | Guides the user to the base, tip and stations, and shows the physical station distances once the length is entered. |
| Nish | Collects the seven values, shows the guidance and validation messages (the report's `message` is written for operators), and displays the result. |
| Scaler | `scale_stub()` / `epics-scale-stub`. |
| Samantha | Inspects the exported GLB independently. `epics-measure-stub scaled.glb` re-detects alignment from the file alone and applies the same definitions. |

## Usage

```bash
uv sync
uv run epics-scale-stub input.glb --measurements measurements.json --output scaled.glb
uv run epics-measure-stub scaled.glb        # independent re-measurement
uv run pytest -q
```

Python: `epics_geometry.stub.scale_stub(input_path, measurements, output_path,
report_path=None, overwrite=False, limits=FitLimits())` returns the report dict.
`measurements` can be a path, a dict, or a `StubMeasurements`.

- The input GLB is never modified. The report records its SHA-256 before and
  after the run.
- Existing outputs are not overwritten unless you pass `--overwrite`. With
  `--overwrite`, the old output GLB and report are **deleted before the run
  starts**. A failed or interrupted run therefore never leaves an older mesh
  next to a new failure report.
- The report must be a `.json` path. Input, output, report and the temporary
  `.<output>.partial.glb` must all be different files. Otherwise the run stops
  before touching anything (exit code 2).
- On success, the report records the output GLB's SHA-256. Treat a mesh as
  this run's result only if its hash matches a report whose `status` is
  `measurements_matched`.
- The GLB is written to a temporary name, reopened and re-measured, and only
  renamed to `--output` if every value is within tolerance. A failed run writes
  only the report (`<output stem>.report.json` by default) and no mesh.
- Exit codes: 0 means matched, 1 means a diagnostic failure (see the report),
  2 means a usage or path error.

## Method

1. Apply the scene-graph transforms (including mirrored nodes) and merge
   vertices by position, so seams don't split the surface.
2. Alignment. Use area-weighted PCA for the long axis. At each end, measure the
   flat cap or flat open boundary area against the nearby cross-section area:
   a ratio of at least 0.5 counts as a base and at most 0.2 as a tip. Exactly
   one base and a non-flat opposite end are required, and the base must be
   within 25° of a clear long axis. For short, wide stubs with a weak PCA axis,
   the base normal defines the axis. The canonical frame has the base plane at
   Z=0, the tip toward +Z, and X along the principal transverse direction.
3. Straightness. Sections at 10–90% of the length must each be one closed
   loop. The centreline may lean at most 15° from the base normal and deviate
   at most 5% of the length from a straight line. Otherwise the result is
   `alignment_ambiguous`.
4. Scale uniformly to the measured length.
5. Slice at the three stations. Circumference is the convex-outline (taut-tape)
   perimeter, and max width is the largest span (the caliper diameter).
6. Fit two positive transverse factors (X, Y) per station, using bounded
   least squares from 16 starts. Among the exact solutions, keep the one with
   the least `log(sx)² + log(sy)²`. The measurements don't say which way the
   widest span points, so mirror-image ties prefer stretching along X.
7. Blend the factors along the length with SciPy's `PchipInterpolator`, which
   doesn't overshoot. Each station's correction is held from the base to 25%
   and from 75% to the tip. Vertices scale about a PCHIP centreline through
   the section centroids. Z, connectivity, UVs and faces are untouched, and
   normals are recomputed across seams.
8. Re-slice the deformed mesh and correct the targets (up to 8 rounds).
   Default limits: factors in [0.70, 1.43] and at most 1.35:1 between the two
   directions (`--min-factor/--max-factor/--max-anisotropy`). Otherwise the
   result is `fit_failed`.
9. Export in metres in the canonical frame. WebP textures are re-encoded as
   PNG losslessly. Check that the texture count and UVs are unchanged. Reopen
   the file, re-detect the alignment, re-measure all seven values, and require
   each to be within max(0.5 mm, 1% of the target).

glTF viewers treat +Y as up, so the canonical +Z stub may appear lying down
in some viewers. The coordinates are still metres.

## Report

The report covers: `status`, `message`, `details`, software versions, the
input hash and `unchanged` flag, the measurement input, station distances, and
the alignment (base type, planar-area ratios, PCA ratio, base/axis angle,
straightness, and the source-to-output matrix before the transverse fit). It
also lists the values after length scaling only, the deformation (station
factors, sampled factor profile, iterations), and `results` (target, actual,
absolute and % error, tolerance, pass/fail for all seven values). The rest
covers verification, timing, warnings and limitations.

| Status | Meaning |
|---|---|
| `measurements_matched` | All seven exported values are within tolerance. This is the only status that writes a mesh. |
| `invalid_measurements` | Missing, malformed or impossible measurements. |
| `unsupported_mesh` | Not a static triangle GLB, extra/background surfaces, or unreadable. |
| `alignment_ambiguous` | No base, several bases, flat tip, oblique base, curved/leaning axis. |
| `section_unsupported` | Open loop (hole), branches/fingers, double-walled shell, strong concavity. |
| `fit_failed` | No stretch matches, needs more than the configured limits, or doesn't converge. |
| `export_mismatch` | Exported file lost textures/UVs or failed re-measurement. |

## Validation log

**Synthetic (automated, `tests/test_stub.py`).** These are deterministic stubs
with flat bases and rounded tips: circular, elliptical, tapered, asymmetric,
open-base, and short-and-wide. Each is distorted with anisotropic transverse
error, taper error, inflation, arbitrary units, random rotation and
translation, and scaled/mirrored node transforms, and all seven values are
recovered within tolerance. The tests also cover metre export, the canonical
frame, UV and texture preservation (PNG and WebP), smooth no-overshoot
factors, every rejection path, no-overwrite behaviour, both CLIs, and the
existing `epics-scale` length-only command.

**Real TRELLIS.2, 2026-10-12.** Input `project_docs/example_photos/amputed
hand.png` (residual hand with one thumb-like digit, plus forearm). Generated
on the Spark's running TRELLIS.2 app (TRELLIS.2-4B, seed 42, resolution 1024,
decimation 500k, texture 2048, WebP). Generation took 132 s and extraction
31 s. The GLB has 484k faces after merging.

- Result: `section_unsupported` in 2.0 s. Loading, WebP textures, scene
  handling and alignment all worked on the real file.
- Expected rejection: this capture is not a valid v1 input (it has a digit and
  extra forearm beyond any chosen base).
- Finding 1: **every cross-section has a second loop about 96% of the outer
  area inside it.** The mesh is a thin, hollow double-walled shell, not a
  single outer surface. The cause isn't confirmed. It may come from the
  TRELLIS.2 `to_glb` remesh step (`remesh=True, remesh_band=1`) or from the
  frame-cropped forearm. If it happens on proper stub captures too, the
  scaler would need an explicit "measure the outer wall" mode. That is a
  decision for the team, not something to enable silently.
- Finding 2: TRELLIS.2 produced **four short finger-like nubs** on this
  residual hand that are not in the photo. This differs from the earlier
  TRELLIS (v1) result recorded in CLAUDE.md.

**Re-check, 2026-10-12, after the path fixes.** The same real GLB (SHA-256
`0e0377a0…61cc0a`) was run again with the current code. It was still rejected
as `section_unsupported` (double-walled shell), with exit code 1, no GLB
written, the input unchanged, and 0.97 s total.

**Real-world validation: not done.** We searched this Mac and the Spark
(TRELLIS.2 checkout, input photos, app temp outputs) on 2026-10-12 and found
**no real, isolated, fingerless-stub photo or TRELLIS.2 GLB**. The only
real-limb sample is the thumb-and-forearm residual hand above, which is not a
valid v1 input. So:

- The success path (align, fit, export, independent re-measure, visual
  inspection) has only been exercised on **synthetic** meshes.
- Nothing here shows that the scaler works on real stub geometry, or that
  TRELLIS.2 produces single-walled stub surfaces it can accept.
- Validation is not finished until a real stub capture is generated, scaled,
  re-measured with `epics-measure-stub`, inspected visually, and checked
  independently by Samantha.

## Remaining limitations

- Matching the seven measurements is a software check that the exported file
  reproduces the entered numbers. It is **not** evidence of anatomical,
  metric, or clinical accuracy. Shape between and beyond the stations is
  inferred by the generator.
- Double-walled shells, fingers/branches, holes at the measured sections,
  strongly concave sections, curved stubs, and meshes without exactly one flat
  base are rejected, not repaired.
- The widest-span direction is not part of the measurements, so it follows
  the mesh's own principal transverse direction.
- Output is in the canonical frame (+Z along the stub). Rhino import and
  orientation have not been tested yet.
