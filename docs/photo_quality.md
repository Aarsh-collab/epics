# Task 4: bad-photo gate

The hosted capture workflow checks the **main generation photo** before queuing
TRELLIS.2. On the Review screen, prepare the photo, approve its preview, then
choose **Check photo quality**. A result with no flags enables generation;
warnings require an explicit acknowledgement. Changing the photo, Start/Tip,
preparation, or end style requires another check.

These are local rule-based checks, not a trained usability score or a guarantee
of anatomical accuracy. No hosted vision API is called. The checks use original,
EXIF-oriented pixels, before the synthetic end face or transparent padding.
When preparation is enabled, they reuse its saved RMBG mask without another
segmentation model call. A mask's ambiguous alpha values are only a proxy for
segmentation uncertainty, not calibrated model confidence.

## Checks and provisional thresholds

| Check | Initial warning rule | Retake guidance |
|---|---|---|
| Resolution | Retained foreground bounding box short side below 128 original pixels | Move closer or upload the full-size original |
| Blur | Grayscale Laplacian variance below 25, measured within the eroded foreground after resizing to at most 768 pixels | Focus on the limb and hold the phone still |
| Exposure | More than 25% of foreground grayscale pixels at 5 or below, or 250 or above | Use even lighting without glare |
| Framing | Retained foreground below 8% of the image, or foreground touches a two-pixel border on the Tip half | Fill more of the frame and include the whole tip |
| Segmentation | No foreground, secondary components above 5%, or ambiguous alpha (0.1–0.9) above 20% of possible foreground (alpha above 0.05) | Check the preview and use a plain background |
| Clutter | Strong Sobel edges above 25% of a nearby background ring, or secondary components above 5% | Remove other objects and use a plain background |

The intentional proximal crop is allowed; it does not itself count as a cropped
tip. Smooth skin can trigger a blur warning, texture/noise can conceal blur, and
lighting/skin tone/background differences affect the checks. An incorrect,
confident mask can pass. The team must calibrate these defaults with approved
photos and downstream generation results before treating strict mode as a
reliable rejection rule. No participant images or calibration results are
included in this change.

Without preparation, blur and exposure are whole-photo estimates and resolution
uses the image dimensions. Framing, segmentation and clutter are explicitly
unavailable; they are never reported as passing. Missing markers also leave tip
framing unavailable. No anatomy is inferred from these checks.

## Enforcement and records

- `EPICS_QUALITY_MODE=soft` is the default. Warnings or missing checks require
  acknowledgement. This avoids silently rejecting photos using uncalibrated rules.
- `EPICS_QUALITY_MODE=strict` blocks warnings and missing checks; acknowledgement
  cannot override it. Without preparation, strict mode therefore cannot pass.
- `POST /api/photo-quality` accepts the main `photo`, `landmarks` (`start` and
  `tip`), and optional `preparation` ID. It requires the existing passcode session,
  checks upload limits, and does not store the uploaded image.
- `POST /api/jobs` recomputes the report before creating a job. In soft mode,
  `quality_ack` must match the returned `review_token`, which binds the photo,
  landmarks, metrics, thresholds and policy. Client-supplied passing reports are
  ignored. A rejection returns HTTP 422 and `photo_quality` with actionable messages.
- The server saves its report and acknowledgement in the job's `input/capture.json`
  and returns it in job status for display with results. It follows existing job
  deletion/retention. The browser's capture JSON includes its current report when
  available. Existing offline CLI/Sol runs are unchanged and do not enforce this
  web-service gate.

## Verification

Synthetic tests cover sharp/blurred foregrounds, bright/dark clipping, small
subjects, cropped tips versus allowed proximal crops, fragmented/uncertain/empty
masks, EXIF rotation, invalid landmarks, and missing-mask limitations. API tests
cover soft acknowledgement, changed inputs, forged passing reports, strict
refusal and strict passing cases, authentication, non-persistence of checks, and
reuse of the prepared mask. Frontend tests cover stale reports, warnings and
non-overridable blocks. Real-photo calibration and live GPU testing remain pending.
