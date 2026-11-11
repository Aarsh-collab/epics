# Task 5: side-photo consistency check

Implements options A and B and [issue #9](https://github.com/Aarsh-collab/epics/issues/9).
The main photo remains required; the side photo remains optional. The side photo
is never supplied to TRELLIS.2 and never changes the mesh. Multi-image generation
(option C) remains outside this task.

## Capture and review

Take the side photo about 90 degrees around the limb from the main view, keeping
the limb in the same pose. Use a plain background, include the whole tip, and aim
the camera broadside rather than along the limb. Both photos must show the same
physical proximal start and distal endpoint.

On Landmarks, the side image has its own Start/Tip buttons, click/drag placement
and numeric percentage fields. Replacing/removing the photo clears these markers.
Missing markers do not block generation: the check will be unavailable. After
generation, review the original side photo, width-profile plot and mean/largest
relative differences. No flag is a consistency result, not verification of accuracy
or clinical fit.

`capture.v1` adds optional `landmarks.side` with `model_start` and `stub_endpoint`,
each an `{x, y}` point in normalized EXIF-oriented image coordinates, top-left
origin. The photo retains role `review` and a unique filename. Older and main-only
captures still work. Keep `capture.json` with every listed original photo for an
offline run. Existing job deletion and retention apply; no external service or
new persistent photo store is used.

## Silhouettes and mesh directions

`side_view.py` uses the existing local `photo_prep.trellis_mask`. It reuses the
hash-checked main-preparation mask when available; otherwise both photos need
masking. Existing preparation checks select the limb and cut at Start, with no
synthetic end face or padding. Missing, fragmented or clipped silhouettes become
unavailable. Widths are sampled perpendicular to Start–Tip at 5%, 10%, ..., 95%,
then divided by the photo's Start–Tip pixel length. A tip more than 8% short of the
foreground endpoint is rejected for this comparison.

The camera direction is not recorded in the current TRELLIS handoff. Rather than
assume the scaler's PCA X axis is the camera axis, the check:

1. Transforms the source GLB into the scaler's canonical frame using
   `source_to_output_matrix_before_transverse_fit` from `scale_report.json`.
2. Fits its projected widths to the **main** silhouette over 0–179 degrees around
   canonical Z and retains acceptable, nearly best-fitting width-axis angles.
3. Reopens the actual scaled GLB, intersects its triangles at the same stations,
   and measures perpendicular views (90 degrees from those main-view axes).
4. Compares those profiles with the held-out side photo. The side image never
   selects which viewing direction looks best. If plausible views disagree on
   whether to warn, the result is unavailable. If all warn, it warns; if none warn,
   no mismatch is flagged. Round shapes can have equivalent views.

All mesh scene transforms are applied. Widths are normalized by mesh length.
Normalization removes uniform image scale, not perspective, foreshortening or
pose changes. Width profiles can miss concavities, offsets and different shapes
with the same total width. If the main photo cannot establish a usable direction,
the report says so instead of guessing.

## Provisional thresholds

| Setting | Default |
|---|---|
| Stations | Every 5%, from 5% to 95% |
| Minimum photo Start–Tip span | 40 pixels |
| Candidate main-view mean / maximum relative fit error | At most 10% / 20% |
| Near-best mean main-view fit allowance | Best error + 1 percentage point |
| Angular sampling | 1 degree, modulo 180 degrees |
| Side warning: mean relative difference | Greater than 12% |
| Side warning: largest relative difference | Greater than 20% |

Relative difference is `abs(mesh_width/mesh_length - photo_width/photo_length)`
divided by `photo_width/photo_length`. Defaults are engineering starting points,
not calibrated acceptance limits. Reports record the thresholds and
`calibration: provisional`. Calibration on approved photos remains necessary.

## Reports and failure behavior

`validation.json.side_view` and `manifest.json.stages.side_view` contain status,
message, thresholds, limitations, normalized profiles, per-station differences,
mean/largest differences and candidate-direction ranges. Completed comparisons
include input-photo hashes. The API exposes this as `result.side_view` for display
beside the photo.

The stage is warning-only. It never changes `scaled.glb`, refuses a mesh, or turns
completed generation into a failure. A discrepancy can raise QA from pass to warn.
Missing photos/markers, invalid silhouettes, uncertain direction and mask-model
failures return unavailable, never pass. If scaling never produces a mesh, the
manifest also reports the comparison unavailable. Dimensional QA can still fail
independently.

## Verification

Tests create non-sensitive images and GLBs in temporary folders, without committing
photos or meshes. They cover round-tube agreement and a D-shaped tube with the same
main-view outline being flagged, EXIF/image-scale invariance, source orientation,
scene transforms, ambiguous views, clipping, missing inputs and mask failures.
Main-mask reuse and hash checks are tested. A complete synthetic rounded-stub
capture runs through real scaling and validation and saves its comparison.
A warning leaves the scaled GLB byte-for-byte unchanged, mask failure leaves
generation complete, and main-only captures still work. Frontend tests cover
landmark export and report labels/metrics; the API exposes unavailable reports.

Live GPU segmentation and approved real-photo calibration remain pending. Tests
inject a deterministic local mask function; they do not validate RMBG on real limbs.
