"""Prepare the generation photo before TRELLIS.2: remove background, cut at Start, finish the end.

Original photo -> foreground mask -> cut across Start, perpendicular to Start->Tip
-> optional synthetic end face -> transparent PNG. The Tip side is never cut or
capped: Tip marks the furthest real limb edge. Retained pixels keep their
original colour; only the ~1 px mask edge, the cut and the end face change.

The end face (``cap="oval"``) is a synthetic visual cue that the limb ends at
Start, not a reconstruction of the real cross-section. It is an ellipse across
the limb at Start whose depth is ``cap_depth`` of the limb width there; it sits
entirely on the retained side of Start, so the Start-to-Tip length is unchanged.
``cap="straight"`` only cuts.

The mask comes from a callable (RGB image -> float alpha in [0, 1]); on the
Spark that is the RMBG-2.0 model TRELLIS.2 itself uses (``trellis_mask``).
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import asdict, dataclass, field
from typing import Callable

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage

PREP_VERSION = "prep.v1"
MIN_LANDMARK_SEPARATION = 0.03  # of the image diagonal
FOREGROUND = 0.5  # model alpha above this is limb
SPECK_SHARE = 0.02  # other foreground below this share of the limb is dropped silently
START_SEARCH = 0.25  # how far along the Start line (x Start-Tip distance) the limb may be from Start
TIP_REACH = 0.08  # the limb must come within this (x Start-Tip distance) of Tip
EDGE_SIGMA = 0.5  # px; antialiasing reaches about one pixel
FACE_LIGHTEN = 0.12  # the end face is a little lighter than the nearby skin

MaskFn = Callable[[Image.Image], np.ndarray]


class PrepError(ValueError):
    """Preparation refused; the message says what the user can change."""


@dataclass(frozen=True)
class PrepSettings:
    cap: str = "straight"  # "straight" or "oval" (experimental; see docs/testing_bugs.md TB-004)
    cap_depth: float = 0.08  # oval depth along the limb, as a fraction of the width at Start
    pad: float = 0.05  # transparent margin, as a fraction of the longer side
    blend: float = 0.25  # width of the face's join into the skin, as a fraction of its depth


@dataclass
class Prepared:
    rgba: Image.Image  # prepared photo, transparent background
    mask: Image.Image  # model alpha before the cut (L), on the oriented photo
    face: Image.Image  # synthetic pixels (L) on the prepared canvas
    record: dict = field(default_factory=dict)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()


def oriented(data: bytes) -> tuple[Image.Image, dict]:
    """Decode a photo and apply its EXIF orientation, as browsers display it."""
    with Image.open(io.BytesIO(data)) as img:
        orientation = img.getexif().get(0x0112, 1)
        raw_size = img.size
        img = ImageOps.exif_transpose(img).convert("RGB")
    return img, {"stored_size": list(raw_size), "exif_orientation": int(orientation), "size": list(img.size)}


def to_pixels(point: dict, size) -> np.ndarray:
    """Normalized top-left image coordinates -> pixel coordinates on the oriented photo."""
    x, y = float(point["x"]), float(point["y"])
    if not (0 <= x <= 1 and 0 <= y <= 1):
        raise PrepError("Start and Tip must lie on the photo")
    return np.array([x * size[0], y * size[1]])


def _runs(values: np.ndarray) -> list[tuple[int, int]]:
    """Index ranges [a, b) where a boolean array is true."""
    edges = np.flatnonzero(np.diff(np.r_[0, values.astype(np.int8), 0]))
    return list(zip(edges[::2], edges[1::2]))


def prepare(photo: bytes, start: dict, tip: dict, mask_fn: MaskFn, settings: PrepSettings = PrepSettings()) -> Prepared:
    if settings.cap not in ("oval", "straight"):
        raise ValueError("cap must be 'oval' or 'straight'")
    image, orientation = oriented(photo)
    w, h = image.size
    s, t = to_pixels(start, image.size), to_pixels(tip, image.size)
    span = float(np.linalg.norm(t - s))
    if span < MIN_LANDMARK_SEPARATION * np.hypot(w, h):
        raise PrepError("Start and Tip are too close together; place Start where the model should begin "
                        "and Tip at the furthest edge of the limb")
    u = (t - s) / span  # along the limb, Start -> Tip
    n = np.array([-u[1], u[0]])  # along the Start line

    alpha = np.clip(np.asarray(mask_fn(image), dtype=np.float64), 0, 1)
    if alpha.shape != (h, w):
        raise ValueError(f"mask is {alpha.shape}, photo is {(h, w)}")
    xs, ys = np.arange(w) + 0.5, (np.arange(h) + 0.5)[:, None]  # pixel centres, broadcast to (h, w)

    def project(origin, axis):
        return (xs - origin[0]) * axis[0] + (ys - origin[1]) * axis[1]

    along = project(s, u)  # signed distance from the Start line, + toward Tip
    across = project(s, n)
    fg = alpha > FOREGROUND

    # Where the limb crosses the Start line: the foreground run nearest to Start.
    samples = np.arange(-np.hypot(w, h), np.hypot(w, h), 0.5)
    line = s + samples[:, None] * n
    inside = (line[:, 0] >= 0) & (line[:, 0] < w) & (line[:, 1] >= 0) & (line[:, 1] < h)
    hit = np.zeros(len(samples), dtype=bool)
    li = line[inside].astype(int)
    hit[inside] = fg[li[:, 1], li[:, 0]]
    runs = [(samples[a], samples[b - 1]) for a, b in _runs(hit)]
    runs = [r for r in runs if r[1] - r[0] >= 2]
    if not runs:
        raise PrepError("The Start line does not cross the limb. Move Start onto the limb, or replace the photo "
                        "if the background removal missed it")
    near = min(runs, key=lambda r: 0 if r[0] <= 0 <= r[1] else min(abs(r[0]), abs(r[1])))
    if not (near[0] <= 0 <= near[1]) and min(abs(near[0]), abs(near[1])) > START_SEARCH * span:
        raise PrepError("Start is not on the limb. Move Start onto the limb where the model should begin")
    lo, hi = near
    width = hi - lo

    # The limb is the connected foreground on the Tip side that this run belongs to.
    keep_side = along >= 0
    labels, count = ndimage.label(fg & keep_side)
    probe = np.clip((s + np.linspace(lo, hi, 64)[:, None] * n + u).astype(int), 0, [w - 1, h - 1])
    seed_ids = labels[probe[:, 1], probe[:, 0]]
    seed_ids = seed_ids[seed_ids > 0]
    if not len(seed_ids):
        raise PrepError("The retained side of Start is empty. Check that Tip is on the far side of the limb")
    limb_id = int(np.bincount(seed_ids).argmax())
    limb = labels == limb_id
    areas = np.bincount(labels.ravel(), minlength=count + 1)
    others = [int(a) for i, a in enumerate(areas) if i not in (0, limb_id)]
    large = [a for a in others if a >= SPECK_SHARE * areas[limb_id]]
    if large:
        raise PrepError(f"Besides the limb, {len(large)} other large object(s) remain on the Tip side of Start "
                        f"(largest {max(large) / areas[limb_id]:.0%} of the limb). Use a photo with a plain "
                        "background, or crop out other objects")
    # Tip must be on the same limb: the nearest limb pixel lies within reach of it.
    limb_px = np.argwhere(limb)
    tip_gap = float(np.min(np.hypot(limb_px[:, 1] + 0.5 - t[0], limb_px[:, 0] + 0.5 - t[1])))
    if tip_gap > TIP_REACH * span:
        raise PrepError("Tip is not on the limb that crosses Start. Place Tip at the furthest edge of the same "
                        "limb, or replace the photo if the background removal cut the limb apart")

    # Silhouette: the limb, a 1 px antialiased edge, cut across Start.
    silhouette = limb.copy()
    face = np.zeros((h, w))
    face_info = None
    cut = np.clip(along + 0.5, 0, 1)  # the Start line, antialiased to a pixel
    if settings.cap == "oval":
        depth = settings.cap_depth * width
        b, a = depth / 2, width / 2  # semi-axes along the limb and across it
        centre = s + ((lo + hi) / 2) * n + u * b  # the face touches Start only at its centre
        du, dn = project(centre, u), project(centre, n)
        r = np.sqrt((du / b) ** 2 + (dn / a) ** 2)
        in_face = r <= 1
        # The near half of the ellipse is the new outline; the rest of the end stays limb.
        corner = (du < 0) & ~in_face & (along < b) & (np.abs(across - (lo + hi) / 2) <= a + 1)
        silhouette &= ~corner
        # Face colour: nearby skin mirrored across the far edge of the face, lightened a little.
        step = 2 * (b - du) + depth * 0.5
        mx = np.clip((xs + u[0] * step).astype(int), 0, w - 1)
        my = np.clip((ys + u[1] * step).astype(int), 0, h - 1)
        rgb = np.asarray(image, dtype=np.float64)
        source = rgb[my, mx]
        band = limb & (along > depth) & (along < depth + 0.2 * width) & (np.abs(across - (lo + hi) / 2) < a)
        mean = rgb[band].mean(axis=0) if band.any() else rgb[limb].mean(axis=0)
        tone = 0.6 * source + 0.4 * mean
        tone = tone + FACE_LIGHTEN * (255 - tone)
        # Blend weight: 1 inside the face, falling to 0 across the join on the limb side.
        feather = max(2.0, settings.blend * depth)
        dist_out = (r - 1) * min(a, b)  # approximate distance outside the ellipse, px
        weight = np.where(in_face, 1.0, np.clip(1 - dist_out / feather, 0, 1))
        weight[du < 0] = in_face[du < 0]  # the outline side has no join; it is the silhouette edge
        weight *= silhouette
        face = weight
        face_info = {"centre_px": centre.tolist(), "width_px": width, "depth_px": depth,
                     "semi_axes_px": [a, b], "join_px": feather}

    # A 3x3 Gaussian: only the pixel rows either side of the boundary change.
    edge = ndimage.gaussian_filter(silhouette.astype(np.float64), EDGE_SIGMA, truncate=2.0)
    edge[ndimage.binary_erosion(silhouette)] = 1  # interior pixels stay opaque
    edge[~ndimage.binary_dilation(silhouette)] = 0
    final_alpha = np.clip(np.minimum(edge, cut), 0, 1)
    out = np.asarray(image, dtype=np.float64).copy()
    if settings.cap == "oval":
        out = out * (1 - face[..., None]) + tone * face[..., None]

    # Transparent margin, so a tip at the photo edge is not flush with the frame.
    pad = int(round(settings.pad * max(w, h)))
    canvas = np.zeros((h + 2 * pad, w + 2 * pad, 4), dtype=np.uint8)
    canvas[pad:pad + h, pad:pad + w, :3] = np.clip(np.rint(out), 0, 255).astype(np.uint8)
    canvas[pad:pad + h, pad:pad + w, 3] = np.rint(final_alpha * 255).astype(np.uint8)
    face_canvas = np.zeros(canvas.shape[:2], dtype=np.uint8)
    face_canvas[pad:pad + h, pad:pad + w] = np.rint(face * 255).astype(np.uint8)

    warnings = []
    border = np.zeros_like(limb)
    border[[0, -1], :] = border[:, [0, -1]] = True
    touching = limb & border & (along > 0.5 * span)
    if touching.any():
        warnings.append("The limb touches the photo edge on the Tip side. Padding was added, but anatomy cut off "
                        "by the camera cannot be recovered; retake the photo with the whole tip in frame")
    if np.abs(across[limb] - (lo + hi) / 2).max() > 3 * width:
        warnings.append("The retained shape is much wider than the limb at Start; check the preview for "
                        "background left attached to the limb")

    record = {
        "version": PREP_VERSION,
        "settings": asdict(settings),
        "orientation": orientation,
        "landmarks_normalized": {"start": dict(start), "tip": dict(tip)},
        "transform": {
            "start_px": s.tolist(), "tip_px": t.tolist(),
            "direction": u.tolist(), "start_to_tip_px": span,
            "canvas_offset_px": [pad, pad], "canvas_size": [w + 2 * pad, h + 2 * pad],
            "note": "pixel = normalized * oriented size; prepared canvas = pixel + offset",
        },
        "start_crossing_px": {"from": lo, "to": hi, "width": width},
        "face": face_info,
        "limb_pixels": int(limb.sum()),
        "dropped_specks": len(others),
        "tip_gap_px": tip_gap,
        "changed_pixels": {
            "edge_band": int(((final_alpha > 0) & (final_alpha < 1)).sum()),
            "face": int((face > 0).sum()),
        },
        "warnings": warnings,
    }
    mask_img = Image.fromarray(np.rint(alpha * 255).astype(np.uint8), "L")
    return Prepared(Image.fromarray(canvas, "RGBA"), mask_img, Image.fromarray(face_canvas, "L"), record)


def preview(prepared: Prepared, outline: bool = False, grey: int = 128) -> Image.Image:
    """The prepared photo on neutral grey; ``outline`` tints the synthetic face."""
    rgba = np.asarray(prepared.rgba, dtype=np.float64)
    a = rgba[..., 3:4] / 255
    rgb = rgba[..., :3] * a + grey * (1 - a)
    if outline:
        f = np.asarray(prepared.face, dtype=np.float64) / 255
        ring = (f > 0) & ~ndimage.binary_erosion(f > 0.5, iterations=2)
        rgb[ring] = [230, 0, 200]
        rgb = rgb * (1 - 0.25 * f[..., None]) + np.array([230, 0, 200]) * 0.25 * f[..., None]
    return Image.fromarray(np.clip(np.rint(rgb), 0, 255).astype(np.uint8), "RGB")


_mask_model = None


def trellis_mask(image: Image.Image) -> np.ndarray:
    """Foreground alpha from the background-removal model TRELLIS.2 ships with (needs CUDA)."""
    global _mask_model
    if _mask_model is None:
        from trellis2.pipelines.rembg import BiRefNet

        _mask_model = BiRefNet(model_name="briaai/RMBG-2.0")
        _mask_model.cuda()
    out = _mask_model(image.copy())
    return np.asarray(out, dtype=np.float64)[..., 3] / 255
