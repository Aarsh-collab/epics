"""Warning-only, orthographic side-outline consistency check (issue #9).

Camera roll is inferred from the MAIN photo and the unscaled source mesh only.
The side photo is held out of that fit. Ambiguous rolls with conflicting side
verdicts are unavailable, not a passing best-fit view. No mesh is modified.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

from . import photo_prep, stub_geometry as sg
from .glb_io import load_instances

FRACTIONS = np.arange(1, 20, dtype=float) / 20  # exclude the cut and rounded endpoint
LIMITATIONS = [
    "Side-outline consistency only; this does not verify anatomy, accuracy or clinical fit.",
    "Assumes straight, stationary limb, matching Start/Tip and approximately perpendicular, broadside photos.",
    "Normalising by length removes uniform image scale, not perspective, foreshortening or pose differences.",
    "Camera roll is estimated from the main silhouette, not calibrated; the side photo never selects the best view.",
    "Equal widths can hide contour offsets, concavities or different shapes; thresholds need real-photo calibration.",
]


@dataclass(frozen=True)
class Settings:
    max_relative_difference: float = .20
    mean_relative_difference: float = .12
    main_mean_fit_limit: float = .10
    main_max_fit_limit: float = .20
    orientation_fit_slack: float = .01
    angle_step_degrees: int = 1
    min_photo_length_px: float = 40


def unavailable(reason: str, settings: Settings = Settings()) -> dict:
    return {"version": "side_view.v1", "status": "unavailable", "message": reason,
            "thresholds": asdict(settings), "calibration": "provisional",
            "fractions": FRACTIONS.tolist(), "limitations": LIMITATIONS.copy()}


def _points(landmarks: dict) -> tuple[dict, dict]:
    points = [landmarks[k] for k in ("model_start", "stub_endpoint")]
    for p in points:
        if not isinstance(p, dict) or any(isinstance(p.get(k), bool) for k in ("x", "y")):
            raise ValueError("Start and Tip must be normalized image coordinates")
        a = np.asarray([p["x"], p["y"]], dtype=float)
        if not np.isfinite(a).all() or np.any(a < 0) or np.any(a > 1):
            raise ValueError("Start and Tip must be finite coordinates between 0 and 1")
    return points[0], points[1]


def photo_profile(data: bytes, landmarks: dict, mask_fn, settings: Settings = Settings()) -> dict:
    """Cut the original mask at Start (no synthetic face), sample perpendicular widths."""
    start, tip = _points(landmarks)
    image, _ = photo_prep.oriented(data)
    size = np.asarray(image.size)
    s, t = np.array([start["x"], start["y"]]) * size, np.array([tip["x"], tip["y"]]) * size
    length = float(np.linalg.norm(t - s))
    if length < settings.min_photo_length_px:
        raise ValueError("Start-to-Tip span is too small; use a closer, full-size photo")
    prepared = photo_prep.prepare(data, start, tip, mask_fn, photo_prep.PrepSettings(cap="straight", pad=0))
    if prepared.record["warnings"]:
        raise ValueError("Silhouette needs review: " + "; ".join(prepared.record["warnings"]))
    alpha = np.asarray(prepared.rgba)[..., 3] / 255.0
    u = (t - s) / length
    ys, xs = np.nonzero(alpha > .5)
    along = (xs + .5 - s[0]) * u[0] + (ys + .5 - s[1]) * u[1]
    if along.size and along.max() > length * 1.08:
        raise ValueError("Tip is not at the furthest limb end; correct the marker before comparing")
    normal = np.array([-u[1], u[0]])
    offsets = np.arange(-np.linalg.norm(size), np.linalg.norm(size), .5)
    widths = []
    for fraction in FRACTIONS:
        line = s + fraction * (t - s) + offsets[:, None] * normal
        samples = ndimage.map_coordinates(alpha, [line[:, 1] - .5, line[:, 0] - .5], order=1, mode="constant", cval=0) > .5
        edges = np.flatnonzero(np.diff(np.r_[False, samples, False]))
        runs = [(a, b) for a, b in zip(edges[::2], edges[1::2]) if b - a >= 2]
        if len(runs) != 1:
            raise ValueError(f"No single continuous silhouette at {fraction:.0%}; check the mask and markers")
        a, b = runs[0]
        ends = line[[a, b - 1]]
        if np.any(ends <= 1) or np.any(ends >= size - 1):
            raise ValueError("The limb outline is clipped by the photo edge")
        widths.append(float((b - a) * .5 / length))
    return {"width_over_length": widths, "length_px": length, "image_size_px": size.tolist()}


def mesh_sections(path: Path, matrix: np.ndarray | None = None) -> list[np.ndarray]:
    """Reopen the GLB, apply scene transforms, and intersect each canonical-Z station."""
    instances, _ = load_instances(path)
    topology = sg.build_topology(instances, drop_fragments=True)
    vertices = topology.vertices
    if matrix is not None:
        matrix = np.asarray(matrix, dtype=float)
        if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
            raise ValueError("Missing or invalid source-to-scaled orientation")
        vertices = vertices @ matrix[:3, :3].T + matrix[:3, 3]
    bottom, top = float(vertices[:, 2].min()), float(vertices[:, 2].max())
    length = top - bottom
    if not np.isfinite(length) or length <= 0:
        raise ValueError("The mesh has no usable length")
    sections = []
    for fraction in FRACTIONS:
        section = sg.slice_mesh(vertices[:, :2], vertices[:, 2] - (bottom + fraction * length), topology.faces, length)
        if section.open_chains or len(section.loops) != 1:
            raise ValueError(f"Mesh has an open or multiple outline at {fraction:.0%}")
        sections.append(section.loops[0] / length)
    return sections


def _widths(sections: list[np.ndarray], angles: np.ndarray) -> np.ndarray:
    radians = np.deg2rad(angles)
    directions = np.stack([np.cos(radians), np.sin(radians)])
    return np.stack([np.ptp(section @ directions, axis=0) for section in sections], axis=1)


def compare(main: dict, side: dict, source: list[np.ndarray], scaled: list[np.ndarray], settings: Settings = Settings()) -> dict:
    """Infer main-view transverse width axes, then evaluate perpendicular widths."""
    report = unavailable("", settings)
    main_widths, side_widths = np.asarray(main["width_over_length"]), np.asarray(side["width_over_length"])
    if main_widths.shape != FRACTIONS.shape or side_widths.shape != FRACTIONS.shape or not np.isfinite([main_widths, side_widths]).all() or np.any(main_widths <= 0) or np.any(side_widths <= 0):
        raise ValueError("Every profile station needs a finite positive width")
    angles = np.arange(0, 180, settings.angle_step_degrees, dtype=float)
    errors = np.abs(_widths(source, angles) - main_widths) / main_widths
    mean_errors, max_errors = errors.mean(axis=1), errors.max(axis=1)
    eligible = (mean_errors <= settings.main_mean_fit_limit) & (max_errors <= settings.main_max_fit_limit)
    report["photo_profiles"] = {"main": main, "side": side}
    if not eligible.any():
        report["message"] = "The main photo and source mesh do not establish a usable viewing direction; review the pose, markers and generated shape."
        return report
    best = int(np.argmin(np.where(eligible, mean_errors, np.inf)))
    candidates = eligible & (mean_errors <= mean_errors[best] + settings.orientation_fit_slack)
    candidate_angles = angles[candidates]
    # A side view is perpendicular to the MAIN camera; do not fit angles to the side image.
    mesh_widths = _widths(scaled, candidate_angles + 90)
    differences = np.abs(mesh_widths - side_widths) / side_widths
    means, maxima = differences.mean(axis=1), differences.max(axis=1)
    warnings = (means > settings.mean_relative_difference) | (maxima > settings.max_relative_difference)
    best_index = int(np.flatnonzero(candidate_angles == angles[best])[0])
    report.update(
        orientation={"method": "main_silhouette_fit_then_perpendicular", "assumed_camera_angle_degrees": 90,
                     "main_width_axis_degrees": float(angles[best]), "side_width_axis_degrees": float((angles[best] + 90) % 180),
                     "candidate_main_angles_degrees": candidate_angles.tolist(), "main_mean_relative_fit": float(mean_errors[best])},
        mean_relative_difference=float(means[best_index]), max_relative_difference=float(maxima[best_index]),
        candidate_mean_relative_range=[float(means.min()), float(means.max())],
        candidate_max_relative_range=[float(maxima.min()), float(maxima.max())],
        mesh_width_over_length=mesh_widths[best_index].tolist(),
        mesh_width_range={"min": mesh_widths.min(axis=0).tolist(), "max": mesh_widths.max(axis=0).tolist()},
        relative_differences=differences[best_index].tolist(),
    )
    if warnings.any() and not warnings.all():
        report["message"] = "The main photo leaves the camera direction ambiguous and plausible side views disagree. Human review is required."
    elif warnings.all():
        report.update(status="warn", message="The side outline differs from every plausible perpendicular mesh view. Review the photo pose, landmarks and mesh shape.")
    else:
        report.update(status="pass", message="No side-width mismatch found within the provisional thresholds. This is a consistency check, not verification of accuracy.")
    return report


def evaluate_capture(capture_dir: Path | None, capture: dict | None, source: Path, scaled: Path,
                     scale_report: dict, *, mask_fn=None, settings: Settings = Settings()) -> dict:
    """Optional stage: every failure becomes an unavailable report, never a mesh failure."""
    try:
        if not capture_dir or not capture:
            return unavailable("No capture photos supplied.", settings)
        side = next((p for p in capture.get("photos", []) if p.get("role") == "review"), None)
        if side is None:
            return unavailable("No side photo supplied.", settings)
        landmarks = capture.get("landmarks") or {}
        side_points = landmarks.get("side")
        if not side_points:
            return unavailable("Side Start and Tip were not supplied.", settings)
        _points(side_points)
        _points(landmarks)
        main = next(p for p in capture["photos"] if p.get("role") == "generation")
        root = Path(capture_dir).resolve()

        def photo_data(entry):
            path = (root / entry["filename"]).resolve()
            if path.parent != root:
                raise ValueError("Photo filename must stay inside the capture folder")
            return path.read_bytes()

        main_data, side_data = photo_data(main), photo_data(side)
        matrix = scale_report["alignment"]["source_to_output_matrix_before_transverse_fit"]
        main_mask_fn = mask_fn or photo_prep.trellis_mask
        if capture.get("preparation"):
            prep = root / "prep"
            record = json.loads((prep / "prep.json").read_text())
            expected = {"start": landmarks["model_start"], "tip": landmarks["stub_endpoint"]}
            if record["original_sha256"] != hashlib.sha256(main_data).hexdigest() or record["landmarks_normalized"] != expected:
                raise ValueError("The main-photo mask no longer matches its photo and markers")
            mask_data = (prep / "mask.png").read_bytes()
            if record["sha256"]["mask.png"] != hashlib.sha256(mask_data).hexdigest():
                raise ValueError("The stored main-photo mask has changed")
            with Image.open(prep / "mask.png") as image:
                alpha = np.asarray(image, dtype=float) / 255
            main_mask_fn = lambda image: alpha
        main_profile = photo_profile(main_data, landmarks, main_mask_fn, settings)
        side_profile = photo_profile(side_data, side_points, mask_fn or photo_prep.trellis_mask, settings)
        result = compare(main_profile, side_profile, mesh_sections(source, matrix), mesh_sections(scaled), settings)
        result["input_sha256"] = {"main_photo": hashlib.sha256(main_data).hexdigest(), "side_photo": hashlib.sha256(side_data).hexdigest()}
        return result
    except Exception as exc:
        result = unavailable(f"Side comparison could not be completed: {exc}", settings)
        result["error_type"] = type(exc).__name__
        return result
