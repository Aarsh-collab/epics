# Task 5: optional side photo for human review

The main photo is required. A side photo is optional and can be replaced or
removed before submission. Only selected photos are listed in `capture.json`
and uploaded; the main photo keeps role `generation`, and the side photo keeps
role `review`. Each file must have a different filename.

The side photo appears on the capture review screen and alongside completed job
results. It is a visual reference for the reviewer, not an input to TRELLIS.2,
scaling, or automated validation. The camera angle and image scale may differ
from the mesh preview. Passing the measurement checks does not verify agreement
with the side photo or clinical fit. When no side photo is supplied, the UI says
that no side-view comparison is performed.

Photos remain in browser memory until submission and clear on reload. Uploaded
photos follow the existing job deletion policy. For an offline handoff, keep
`capture.json` with every original photo listed in it.

This implements roadmap Task 5's recommended first step (A) and makes the
existing review role visible in the results. Automated side-silhouette comparison
with separate side landmarks (B) and multi-image generation experiments (C)
remain future work.

## Verification

- Frontend tests cover main-only upload/export, two-photo roles, removed side
  photos, and rejecting side-only capture readiness.
- API tests run main-only capture through real scaling and validation with a
  synthetic generated mesh. Existing two-photo tests still cover that route.
- Invalid or ambiguous generation/review roles are rejected by the API.
- No participant photos or live GPU inference are needed for these tests.
