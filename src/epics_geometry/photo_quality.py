"""Local, CPU-only photo checks. Defaults are provisional, not a usability classifier."""
from __future__ import annotations

import io
from dataclasses import asdict, dataclass

import numpy as np
from PIL import Image, ImageOps
from scipy import ndimage as ndi


@dataclass(frozen=True)
class QualitySettings:
    analysis_max_side: int = 768
    min_crop_side: int = 128
    min_occupancy: float = 0.08
    min_sharpness: float = 25.0
    max_clipped_share: float = 0.25
    max_uncertain_share: float = 0.20
    max_secondary_share: float = 0.05
    max_clutter_share: float = 0.25


def evaluate(photo: bytes, *, mask=None, start=None, tip=None,
             settings: QualitySettings = QualitySettings()) -> dict:
    """Assess original EXIF-oriented pixels, never synthetic caps/padding.

    mask is the original RMBG alpha in [0,1]. Alpha ambiguity is only a proxy
    for segmentation uncertainty. No mask means limb-specific checks are unavailable.
    """
    with Image.open(io.BytesIO(photo)) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
    w, h = image.size
    if (start is None) != (tip is None):
        raise ValueError("Supply both Start and Tip, or neither.")
    if start is not None:
        points = np.array([[float(p["x"]), float(p["y"])] for p in (start, tip)])
        if not np.isfinite(points).all() or np.any(points < 0) or np.any(points > 1):
            raise ValueError("Quality landmarks must be finite normalized image coordinates.")
        if np.linalg.norm(points[1] - points[0]) <= 0:
            raise ValueError("Quality Start and Tip must be distinct.")
    checks = []
    metrics = {}

    def check(code, failed, message):
        checks.append({"code": code, "status": "warn" if failed else "pass", "message": message})

    def missing(code, message):
        checks.append({"code": code, "status": "unavailable", "message": message})

    alpha = None
    if mask is not None:
        alpha = np.asarray(mask, dtype=float)
        if alpha.shape != (h, w) or not np.isfinite(alpha).all() or np.any((alpha < 0) | (alpha > 1)):
            raise ValueError("Quality mask must match the oriented image and contain values in [0,1].")
    scale = min(1.0, settings.analysis_max_side / max(w, h))
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    gray = np.asarray(image.resize(size, Image.Resampling.BILINEAR).convert("L"), dtype=float)
    if alpha is None:
        for code in ("framing", "segmentation", "clutter"):
            missing(code, "Prepare the photo to check the limb's " + code + ".")
        sharpness = float(np.var(ndi.laplace(gray)))
        clipped = float(np.mean((gray <= 5) | (gray >= 250)))
        check("blur", sharpness < settings.min_sharpness,
              "The photo may be blurry — hold the phone still and focus on the limb. This whole-photo check can be influenced by the background.")
        check("exposure", clipped > settings.max_clipped_share,
              "Large dark or bright areas may hide detail — use even lighting. This whole-photo check can be influenced by the background.")
        check("resolution", min(w, h) < settings.min_crop_side,
              "Photo resolution is low — upload the original full-size photo.")
        metrics["image_size_px"] = [w, h]
        metrics.update(sharpness=sharpness, clipped_share=clipped, region="whole_image")
    else:
        a = np.asarray(Image.fromarray(alpha.astype("float32")).resize(size, Image.Resampling.BILINEAR))
        fg = a > 0.5
        labels, count = ndi.label(fg)
        areas = np.bincount(labels.ravel())[1:]
        # Components are not silently dropped: report fragmentation/clutter.
        secondary = float(1 - areas.max() / areas.sum()) if count else 1.0
        possible = a > 0.05
        uncertain = float(np.mean((a[possible] > .1) & (a[possible] < .9))) if possible.any() else 1.0
        check("segmentation", not count or secondary > settings.max_secondary_share or uncertain > settings.max_uncertain_share,
              "The limb outline is uncertain or split — use a plain background and check the prepared preview.")
        # Measure only the Start-to-Tip side, so the intentional proximal crop is allowed.
        roi = fg.copy()
        tip_edge = None
        if start is not None and tip is not None:
            s = np.array([float(start["x"]), float(start["y"])])
            t = np.array([float(tip["x"]), float(tip["y"])])
            if not np.isfinite([s, t]).all() or np.any(s < 0) or np.any(s > 1) or np.any(t < 0) or np.any(t > 1):
                raise ValueError("Quality landmarks must be finite normalized image coordinates.")
            s *= size
            t *= size
            direction = t - s
            span = np.linalg.norm(direction)
            if span <= 0:
                raise ValueError("Quality Start and Tip must be distinct.")
            ys, xs = np.indices(gray.shape)
            along = ((xs + .5 - s[0]) * direction[0] + (ys + .5 - s[1]) * direction[1]) / span
            roi &= along >= 0
            border = np.zeros_like(fg)
            border[:2, :] = border[-2:, :] = True
            border[:, :2] = border[:, -2:] = True
            tip_edge = bool(np.any(fg & border & (along > .5 * span)))
        else:
            missing("tip_framing", "Place Start and Tip to check whether the tip is cut off.")
        occupancy = float(roi.mean())
        yy, xx = np.nonzero(roi)
        crop_side = int(min(np.ptp(xx) + 1, np.ptp(yy) + 1) / scale) if len(xx) else 0
        check("resolution", crop_side < settings.min_crop_side,
              "The retained limb has too few pixels — move closer or use the original full-size photo.")
        check("framing", occupancy < settings.min_occupancy or bool(tip_edge),
              "The limb is too small or the tip touches the edge — move closer and keep the whole tip in frame.")
        # Exclude silhouette and crop boundaries from sharpness to avoid artificial edges.
        interior = ndi.binary_erosion(roi, iterations=2)
        if interior.sum() < 16:
            missing("blur", "Too little limb area to check sharpness — retake the photo closer.")
            missing("exposure", "Too little limb area to check lighting — retake the photo closer.")
        else:
            sharpness = float(np.var(ndi.laplace(gray)[interior]))
            clipped = float(np.mean((gray[interior] <= 5) | (gray[interior] >= 250)))
            check("blur", sharpness < settings.min_sharpness, "Photo may be blurry — hold the phone still and focus on the limb.")
            check("exposure", clipped > settings.max_clipped_share, "Limb detail is lost in dark or bright areas — use even lighting without glare.")
            metrics.update(sharpness=sharpness, clipped_share=clipped)
        ring = ndi.binary_dilation(roi, iterations=12) & ~ndi.binary_dilation(fg, iterations=3)
        edges = np.hypot(ndi.sobel(gray, axis=0), ndi.sobel(gray, axis=1)) > 100
        clutter = float(edges[ring].mean()) if ring.any() else 0.0
        check("clutter", clutter > settings.max_clutter_share or secondary > settings.max_secondary_share,
              "Other objects or strong edges are near the limb — use a plain background.")
        metrics.update(region="retained_limb", image_size_px=[w, h], crop_short_side_px=crop_side, occupancy=occupancy, tip_touches_edge=tip_edge,
                       uncertain_alpha_share=uncertain, secondary_component_share=secondary, clutter_edge_share=clutter)
    return {"version": "photo_quality.v1", "calibration": "provisional", "checks": checks,
            "metrics": metrics, "thresholds": asdict(settings),
            "limitations": "Heuristic photo checks only; passing does not establish usable or accurate mesh geometry."}


def decision(report: dict, mode: str) -> dict:
    if mode not in ("soft", "strict"):
        raise ValueError("quality_mode must be soft or strict")
    issues = [c for c in report["checks"] if c["status"] != "pass"]
    return {**report, "mode": mode, "status": ("block" if mode == "strict" else "warn") if issues else "pass"}
