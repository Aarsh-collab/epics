# EPICS capture frontend

Local React + TypeScript app. Photos stay in browser memory; nothing is uploaded to a service. Reloading clears the session.

```powershell
cd web
npm install
npm run dev
```

Open the printed localhost URL. `npm run build` checks TypeScript and creates `dist/`. `npm test` checks station interpolation and measurement validation.

## Implemented

Main and side photo selection (JPG/PNG/WebP, 15 MB limit), image decoding, normalized start/tip placement, drag and numeric correction, fixed 25/50/75% station overlays, length and three physical circumferences in millimetres, unreliable-station flags, confirmation, and capture JSON download. Station distances are shown after length entry. Photo pixels never supply physical dimensions.

## Team handoff

`capture.v1` uses normalized image coordinates with top-left origin: x increases rightward, y downward. Each section includes its fraction, interpolated point, physical distance from start, circumference value (null if unreliable), units, and reliability. Original photos are referenced by filename and pixel dimensions; image bytes are not embedded in JSON. Keep the original photos with the export.

Rohan can replace the guidance logic in `src/capture.ts`; markers can be moved as a pair and stations follow at fixed fractions. Independent station fractions are not supported by Aarsh's current contract.

This is not Aarsh's scaler payload: his v1 schema additionally requires three maximum widths and accepts no photo-coordinate fields. An adapter or scaler update is required. The app explicitly marks this incompatibility in the export. Missing measurements may be exported for review but must not be submitted as a complete scaling job.

TRELLIS generation, backend persistence/API, 3D preview, GLB downloads, and Samantha's independent evaluation are not integrated. Do not interpret confirmation or JSON download as a generated/scaled mesh result.
