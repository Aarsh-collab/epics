# EPICS capture frontend

Local React + TypeScript capture flow. Photos stay in browser memory; nothing is uploaded. Reloading clears the session.

```powershell
cd web
npm ci
npm run dev
```

## Verification

```powershell
npm test
npm run build
python scripts/check_scaler_contract.py
```

The export test calls the same `toScalerMeasurements` function used by the UI, validates the resulting JSON against the repository schema, and writes an ignored `.test-output/frontend-measurements.json`. The Python check passes that exact file unchanged to Aarsh's `parse_measurements`. Run tests before the Python check. GitHub Actions runs all three checks.

## Measurements and export

Collect straight length plus circumference and maximum width at fixed 25/50/75% stations, all in mm. Width is the largest straight-across span in the station plane, using calipers or parallel straightedges and a ruler. It is not inferred from the image. Photo percentages are guides only; physical station distances are fraction times entered length.

`Export scaler measurements` produces `stub-measurements.json` matching `schemas/stub_measurements.v1.schema.json`: numeric schema_version 1, mm units, length_mm, and three sections with fraction, circumference_mm, max_width_mm. It includes no photo fields. The UI mirrors the parser's positive-value, length-limit, and circumference/width consistency checks, including its upper-bound tolerance. Server validation remains required once an API exists.

Unreliable stations may proceed to review, but cannot produce a scaler-ready export. Under Developer handoff, a separate `capture.v2` record can retain landmarks, photo references, null values and reliability flags. This record is not scaler input. Both exports require confirmation.

## Team contract and remaining integration

This implements the **existing main-branch v1 schema**, provisionally pending the canonical-contract decision in issue #1. It does not claim that the team has finalized that decision. Keep photo metadata separate; any future contract change must update exporter, tests and adapter together.

Normalized landmarks use top-left origin, x right, y down. Each capture station records a fixed fraction, interpolated photo point and physical distance from the base. Rohan can replace guidance in `src/capture.ts` and instructions in `src/App.tsx`; physical stations must remain consistent with the scaler.

The report panel previews every current scaler and QA overall status with explicit example labels. It also accepts existing report JSON, preserving operator message/summary, warnings and limitations. Imported reports are not authenticated or linked to the current capture/GLB. No report status enables a mesh download. Samantha's Measurements branch is not merged: its input format, width checks and circumference definition still require alignment (issue #3).

TRELLIS generation, backend API/storage, job submission, actual GLB preview/download and automatic independent evaluation are not integrated. Reference geometry does not establish anatomical accuracy or clinical fit.
