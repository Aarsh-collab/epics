# EPICS capture frontend

Local React + TypeScript app. Photos stay in browser memory; nothing is uploaded to a service. Reloading clears the session.

```powershell
cd web
npm install
npm run dev
```

Open the printed localhost URL. `npm run build` checks TypeScript and creates `dist/`. `npm test` checks station interpolation and measurement validation.

## Implemented

Main and side photo selection (JPG/PNG/WebP, 15 MB limit), image decoding, normalized start/tip placement, drag and numeric correction, fixed 25/50/75% station overlays, length plus three physical circumferences and max widths in millimetres, unreliable-station flags, confirmation, and capture JSON download. Station distances are shown after length entry. Photo pixels never supply physical dimensions.

## Team handoff

`capture.v1` uses normalized image coordinates with top-left origin: x increases rightward, y downward. Each section includes its fraction, interpolated point, physical distance from start, circumference value (null if unreliable), units, and reliability. Original photos are referenced by filename and pixel dimensions; image bytes are not embedded in JSON. Keep the original photos with the export.

Rohan can replace the guidance logic in `src/capture.ts`; markers can be moved as a pair and stations follow at fixed fractions. Independent station fractions are not supported by Aarsh's current contract.

The export also embeds `stub_measurements`, the scaler's input (`schemas/stub_measurements.v1.schema.json`): length plus circumference and max width at all three stations. It is `null` when any station is marked unreliable or a circumference/width pair is impossible (`2·w < c ≤ π·w`); such a capture can be reviewed but `epics-pipeline` will not scale it.

TRELLIS.2 generation, scaling and validation run on Sol from the downloaded files (see the root README): copy `capture.json` and both original photos into one folder there. There is no live backend, 3D preview, or GLB download in the browser. Do not interpret confirmation or JSON download as a generated/scaled mesh result.
