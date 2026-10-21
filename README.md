# EPICS prosthetics geometry project

The guided capture frontend lives in [`web/`](web/README.md). Run `npm install` and `npm run dev` from that directory to upload photos, place landmarks, enter physical measurements, and export a review record.

Exploratory workflow for turning a single image into a 3D reference mesh with [TRELLIS.2](https://github.com/microsoft/TRELLIS.2), then checking and adjusting its dimensions against physical measurements.

Current stage: evaluating generated meshes and a measurement-based scaling workflow. A plausible mesh is **not** a verified anatomical scan or a ready-to-fit prosthetic surface. Any use involving a person requires appropriate review and validation.

The upstream TRELLIS.2 checkout, participant/example photos, local planning documents, and machine-specific notes are intentionally not included in this repository.

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
