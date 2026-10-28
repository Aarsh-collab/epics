# Testing bugs and integration gaps

Record observed failures separately from suspected causes. Keep participant or test photos and generated meshes outside the repository unless the team explicitly approves adding them.

## TB-001 — Outer-shell option stops on a mesh without a nested inner wall

- **Reported:** 2026-10-25 by the project team.
- **Observed behavior:** When outer-shell removal is selected but the generated mesh has no removable inner shell, the run errors at the `outer_shell` stage. The code currently requires exactly two connected, nested surfaces and raises if it finds another count.
- **Expected behavior to decide:** A valid single outer surface should be allowed to continue to scaling, with the manifest recording that no inner wall was removed. Other topologies should still fail with a useful diagnostic.
- **Impact:** A generation result that might otherwise be suitable for scaling cannot finish when this option is selected.
- **Code path:** `src/epics_geometry/pipeline.py` calls `outer_shell.extract()` before `scale_stub()`; `src/epics_geometry/outer_shell.py` rejects any connected-surface count other than two.
- **Next test:** Run the same measured capture with this option on and off using a verified single-surface GLB. Compare `manifest.json`, the scale report, and any `scaled.glb` before changing the fallback behavior.
- **Change (2026-10-27):** `outer_shell.extract()` now classifies every connected piece: the largest is the outer wall; a second large piece is removed as an inner wall only if sampled points are inside the outer wall, hidden from 26 outside directions, and the area/volume ratios fit a thin double shell; a single wall continues. Smaller pieces are removed when hidden inside, fused (weld or manifold3d union) when touching/crossing and visible, and removed and flagged when separate (gap above 0.01% of the bounding-box diagonal). Small holes and back-to-back duplicate faces are repaired locally. Refusals stop at `outer_shell` with `cleanup_refused`, keep `generated.glb`, and write `outer_shell_report.json` and `outer_shell_review.glb`.
- **Checked on the nine-piece GLB from the 2026-10-27 run:** piece 1 kept (4 back-to-back faces removed, one 3-edge hole filled), piece 2 removed as inner wall (inside 100%, visible 0%, area ratio 0.977, volume ratio 0.961), pieces 3-9 removed as hidden inside. Output is one closed body; all 247,355 kept faces have unchanged texture colour; tip exterior unchanged in five views. With the earlier fake measurements (200 mm; 190/165/145 circumference) scaling matched and validation returned WARN only for "not watertight", which the validator reports because it counts UV-seam splits (position-merged, the mesh is watertight).
- **Status:** Fixed in code, pending a new website run; a matched measurement does not by itself show the tip is correct.

## TB-002 — Generated arm has no clear flat base for automatic alignment

- **Reported:** 2026-10-25 by the project team, with an input photo and TRELLIS output screenshot attached in the conversation (not copied into this repository).
- **Observed behavior:** The input photo shows a limb continuing beyond the left image edge. The shown generated model appears to complete that end rather than leave a clear flat cutoff; it also appears to add hand-like detail not evident at that end of the input. The exact mesh topology and scaler status have not been inspected.
- **Expected behavior:** The generated mesh used by the v1 stub scaler should have one recognizable flat base at the chosen model start, a rounded opposite tip, and no extra branch or anatomy.
- **Impact:** The scaler may return `alignment_ambiguous` if it cannot identify the base, or it may align/measure a different extent from the user's chosen start and physical measurements. The photo landmarks currently do not crop the input or cut the generated mesh.
- **Code path:** `src/epics_geometry/trellis_run.py` sends the whole main photo to TRELLIS.2; `src/epics_geometry/stub_geometry.py` detects a flat base on the generated GLB; `src/epics_geometry/pipeline.py` does not use the photo landmarks for this step.
- **Next test:** Save and inspect the background-removed image and `generated.glb` before outer-shell removal or scaling. Record the scaler's exact status and alignment report. Test a capture with an explicit intended base, then decide whether the workflow needs a 3D mesh-cut step or different capture guidance; a 2D marker alone does not specify a 3D cutting plane.
- **Marker experiment:** The team reported that the line and keep/cut coloring approach did not work as intended. Its automatic coloring and mesh cut were removed from the app and pipeline on 2026-10-27. Preserve the original test outputs for comparison if available; the exact failure mode still needs to be logged from a run report.
- **Status:** Open. Visual observation only; the generated GLB and run report are needed to confirm the failure stage.

## TB-003 — Side photo is required and uploaded but unused by the 3D pipeline

- **Reported:** 2026-10-25 by the project team.
- **Observed behavior:** The capture flow requires both a main and side photo, includes both in the capture record, uploads both to the service, and shows the side photo during review. Generation selects only the main photo; scaling and validation use the generated mesh and physical measurements. No automated side-view comparison runs.
- **Impact:** Users must supply and upload a second image without improving or checking the current mesh result.
- **Expected behavior to decide:** Make the side photo optional until a defined comparison/validation feature uses it, or implement that feature and report what it checks. Do not imply the current mesh uses two views.
- **Code path:** `web/src/App.tsx` requires `photos.every(Boolean)` and uploads both files; `src/epics_geometry/pipeline.py` selects only the photo with role `generation`.
- **Next test:** Run capture with a main photo only after deciding the intended workflow; verify capture export, server validation, generation, and review all handle an absent side photo. If keeping it required, specify and test the side-view analysis it supports.
- **Status:** Open. Confirmed from the current code.

## TB-004 — Photo preparation (cut at Start) before TRELLIS: comparison

- **Date:** 2026-10-27. **Input:** `~/epics-app/test-inputs/arm_clean.png` (the current arm example; the only real limb photo on the Spark), Start (0.30, 0.53), Tip (0.91, 0.49). TRELLIS.2-4B, seed 42, resolution 1024, remesh on, identical for all three. Outputs: `~/epics-app/experiments/prep-compare/arm_clean/`.
- **Original photo:** the forearm plus a large invented branch where the photo runs on past the elbow, and stray pieces. Cleanup succeeded; scaling stopped with `alignment_ambiguous` (no flat base).
- **Straight cut:** one forearm tube, flat at Start, tip nub kept, no invented branch. TRELLIS returned the outer and inner walls joined at the cut rim into one surface, so cleanup refused it (it separates only disconnected walls), and the scaler without cleanup stopped on a 0.3% stray piece.
- **Oval face (8% depth):** one forearm tube, but with an invented lump at the Start end (95th-percentile radius 0.14 against about 0.08 along the rest of the tube). Same joined-wall result.
- **Extra seeds (1 and 7, prepared inputs only):** straight and oval both gave a single forearm tube without a lump or invented branch, so the seed-42 lump was not repeated. Seed 7 gave a flat-bottomed, D-shaped cross-section for both: the single view leaves the hidden side unconstrained.
- **Decision:** straight cut is the website default; the oval is offered as experimental only. Neither prepared input scaled yet: the next mesh step is to split walls that meet at the cut rim. Clearer photos are still needed for the comparison the plan asks for.
