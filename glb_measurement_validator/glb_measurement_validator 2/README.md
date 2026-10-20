# GLB Measurement Validator

Engineering QA helper that compares a submitted length and 25%/50%/75% circumference measurements against an exported GLB mesh. It does not rescale the mesh to match the submitted values and does not make clinical suitability claims.

## Requirements

Python 3.10+ recommended.

```bash
python -m venv .venv
# macOS/Linux
source .venv/bin/activate
# Windows PowerShell: .venv\\Scripts\\Activate.ps1
pip install -r requirements.txt
```

## Input JSON

```json
{
  "length_mm": 120,
  "stations": [
    {"name": "25%", "circumference_mm": 200, "point_location": "25% from proximal cut", "units": "mm"},
    {"name": "50%", "circumference_mm": 200, "point_location": "50% from proximal cut", "units": "mm"},
    {"name": "75%", "circumference_mm": null, "unmeasurable": true, "point_location": "75% from proximal cut", "units": "mm"}
  ]
}
```

Stations marked `unmeasurable: true` or with `circumference_mm: null` are skipped and reported as `not_verified`.

## Run

```bash
python generate_fixtures.py
python validate_glb.py fixtures/known_cylinder_120mm_200mm_circumference.glb fixtures/submitted_known.json --mesh-units m --axis-mode fixed --axis z --proximal-end min -o report.json
```

The fixtures are centered on Z, so `--axis z --proximal-end min` is appropriate. For real exports, agree on units, axis convention, and which end is proximal with the scaling team. Defaults are meters, PCA long axis, and minimum projection as proximal; do not rely on these defaults without confirming them.

Options:
- `--mesh-units m|cm|mm`: units of the mesh coordinates before conversion to mm.
- `--axis-mode pca|fixed`: PCA finds the dominant axis; fixed uses `--axis`.
- `--axis x|y|z|-x|-y|-z`: fixed axis only.
- `--proximal-end min|max`: which end of the selected axis is proximal.
- `--pass-mm 2 --warn-mm 5`: team-defined absolute-difference thresholds. Up to pass threshold is `ok`; above pass through warn threshold is `flagged`; above warn is `fail`.
- `--spread-offset-pct 2`: also samples at ±2 percentage points around each station and reports the spread when both neighboring sections are measurable.

## Report

The report contains `overall_status`, length comparison, station comparisons, unit/orientation assumptions, mesh diagnostics, tolerances, `not_verified`, and a short `summary`. Differences are `measured - submitted`; percentage differences use submitted value as denominator.

## Important limitations / assumptions

- GLB scene graph transforms are baked in before measurements.
- Coordinates are converted to mm using the declared `--mesh-units`; no fitting/rescaling is performed.
- PCA's dominant direction can be wrong for curved, short, or branched shapes. Cross-check with the exporter convention and inspect the axis vector.
- The code measures the perimeter of exactly one closed cross-section loop. Missing, open, or multiple loops are flagged instead of guessed.
- PCA direction sign is mathematically ambiguous; `--proximal-end` defines which extreme is proximal after a deterministic sign choice. For production use, a semantic proximal marker or agreed fixed axis is safer.
- The nominal fixture is a faceted cylinder, so measured perimeter approaches but may not equal the analytic circumference exactly. With 512 sections, the polygon perimeter error is small. Do not treat the fixture as a substitute for testing scene transforms and real export behavior.
- Mesh checks are diagnostics, not automatic repair. Boundary edges may be expected at the proximal cut depending on the modeling workflow.
- Tolerances are engineering-team choices, not clinical thresholds.
- Not verified: tape-measure accuracy, endpoint selection from photo, fit/comfort/clinical suitability, mesh fidelity to the actual limb, or side-photo appearance.

## Suggested production checks before deployment

1. Confirm Rohan's exact submitted JSON schema and point-location semantics.
2. Confirm the output schema with Nish.
3. Confirm GLB units, axis/up-axis, and proximal endpoint with the scaling-code owner.
4. Run known-good, wrong-scale, broken-mesh, and real exported GLBs.
5. Review warning wording and status aggregation with the team.
6. Pin tested dependency versions before deployment.
