# EPICS capture frontend

React + TypeScript app. Photos stay in browser memory until the user submits a capture; submitting uploads the main photo, the optional side photo if selected, and the record to the EPICS service (`src/epics_geometry/server.py`, hosted on the lab DGX Spark). Reloading clears an unsubmitted session. Leaving or reloading the result page deletes the job (photos and meshes) on the server; the browser asks first if nothing was downloaded.

```powershell
cd web
npm install
npm run dev
```

Open the printed localhost URL. `npm run build` checks TypeScript and creates `dist/`. `npm test` checks station interpolation, measurement validation, and optional-photo handoff.

## Implemented

Required main and optional side photo selection (JPG/PNG/WebP, 15 MB limit), image decoding, normalized start/tip placement, drag and numeric correction, fixed 25/50/75% station overlays, length plus three physical circumferences and max widths in millimetres, unreliable-station flags, confirmation, and capture JSON download. Station distances are shown after length entry. Photo pixels never supply physical dimensions.

## Team handoff

The Landmarks step asks for independent Side Start and Side Tip when a side photo
is present. These are exported as `landmarks.side`; replacing/removing the side
photo clears them. Missing markers never block generation. Results show the
warning-only side-outline report and normalized width plot beside the photo;
missing inputs or uncertain viewing direction are explicitly unavailable. See
[method, limitations and provisional thresholds](../docs/side_photo.md).

`capture.v1` uses normalized image coordinates with top-left origin: x increases rightward, y downward. Each section includes its fraction, interpolated point, physical distance from start, circumference value (null if unreliable), units, and reliability. Original photos are referenced by filename and pixel dimensions; image bytes are not embedded in JSON. Keep the original photos with the export.

Rohan can replace the guidance logic in `src/capture.ts`; markers can be moved as a pair and stations follow at fixed fractions. Independent station fractions are not supported by Aarsh's current contract.

The export also embeds `stub_measurements`, the scaler's input (`schemas/stub_measurements.v1.schema.json`): length plus circumference and max width at all three stations. It is `null` when any station is marked unreliable or a circumference/width pair is impossible (`2·w < c ≤ π·w`); such a capture can be reviewed but `epics-pipeline` will not scale it.

After the passcode, **Generate reference mesh** submits the capture; the job page polls status and links `scaled.glb` (metres), `manifest.json` and the reports. Every result is pending human review. **Download capture.json only** still supports the Sol route (see the root README). The result page previews available meshes and shows the optional side photo for human review. With side landmarks, the pipeline adds a warning-only outline consistency report after scaling; it never changes the mesh. See [Task 5 behavior](../docs/side_photo.md). `npm run dev` proxies `/api` to a local `epics-server` on port 8000.
