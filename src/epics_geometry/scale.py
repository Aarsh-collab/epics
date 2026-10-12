"""Apply a measured overall length to an image-generated GLB.

This is a deliberately limited first stage. It applies *uniform* scaling, so it
does not assert that local cross-sections or the unseen surface are correct.
GLB/glTF coordinates are written in metres; input measurements are millimetres.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import trimesh


AXES = {"x": 0, "y": 1, "z": 2}


def scale_length(
    input_path: Path,
    output_path: Path,
    *,
    length_mm: float,
    axis: str,
    proximal_end: str,
) -> dict:
    """Scale a GLB uniformly and re-read its exported bounds for verification."""
    if not np.isfinite(length_mm) or length_mm <= 0:
        raise ValueError("length_mm must be a positive finite number")
    if axis not in AXES:
        raise ValueError("axis must be x, y, or z")
    if proximal_end not in {"min", "max"}:
        raise ValueError("proximal_end must be min or max")
    if input_path.resolve() == output_path.resolve():
        raise ValueError("output path must differ from input path")
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if input_path.suffix.lower() != ".glb" or output_path.suffix.lower() != ".glb":
        raise ValueError("input and output must both be .glb files")

    scene = trimesh.load_scene(input_path)
    bounds = scene.bounds
    if bounds is None or not np.isfinite(bounds).all():
        raise ValueError("GLB has no finite geometry bounds")
    i = AXES[axis]
    source_length = float(bounds[1, i] - bounds[0, i])
    if source_length <= 0:
        raise ValueError("GLB has no extent along the selected length axis")

    # A GLB uses metres. TRELLIS' coordinates have arbitrary physical scale.
    target_metres = length_mm / 1000.0
    factor = target_metres / source_length
    transform = np.eye(4)
    transform[:3, :3] *= factor
    # Put the marked proximal boundary at zero for consistent downstream slices.
    proximal_coordinate = bounds[0, i] if proximal_end == "min" else bounds[1, i]
    transform[i, 3] = -factor * proximal_coordinate
    scene.apply_transform(transform)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    scene.export(output_path, file_type="glb")

    exported = trimesh.load_scene(output_path)
    exported_bounds = exported.bounds
    if exported_bounds is None or not np.isfinite(exported_bounds).all():
        raise RuntimeError("exported GLB has no finite geometry bounds")
    observed_mm = float((exported_bounds[1, i] - exported_bounds[0, i]) * 1000.0)
    if not np.isclose(observed_mm, length_mm, rtol=1e-5, atol=0.01):
        raise RuntimeError(
            f"export length mismatch: target {length_mm:.3f} mm, got {observed_mm:.3f} mm"
        )

    report = {
        "status": "overall_length_scaled_only",
        "input_glb": str(input_path),
        "output_glb": str(output_path),
        "length_axis": axis,
        "proximal_end": proximal_end,
        "target_length_mm": length_mm,
        "exported_length_mm": observed_mm,
        "scale_factor": factor,
        "exported_extents_mm_xyz": (exported.extents * 1000.0).tolist(),
        "units": "millimetres in report; metres in GLB coordinates",
        "unverified": [
            "circumferences and local cross-sections were not adjusted",
            "unseen anatomy and wearer-contact fit were not verified",
        ],
    }
    report_path = output_path.with_suffix(".json")
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_glb", type=Path)
    parser.add_argument("output_glb", type=Path)
    parser.add_argument("--length-mm", type=float, required=True)
    parser.add_argument("--axis", choices=AXES, required=True)
    parser.add_argument("--proximal-end", choices=("min", "max"), required=True)
    args = parser.parse_args()
    report = scale_length(
        args.input_glb,
        args.output_glb,
        length_mm=args.length_mm,
        axis=args.axis,
        proximal_end=args.proximal_end,
    )
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
