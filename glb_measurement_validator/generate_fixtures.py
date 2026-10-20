#!/usr/bin/env python3
"""Generate deterministic GLB fixtures and matching submitted measurement JSON.

The main fixture is a 120 mm long cylinder with 200 mm circumference.
Additional fixtures include a deliberately 10x oversized cylinder and an open/broken
cylinder whose side wall has no end caps.
"""
from __future__ import annotations
import json
from pathlib import Path
import trimesh

OUT = Path("fixtures")
OUT.mkdir(exist_ok=True)

LENGTH_MM = 120.0
CIRCUMFERENCE_MM = 200.0
RADIUS_MM = CIRCUMFERENCE_MM / (2.0 * 3.141592653589793)


def export(mesh: trimesh.Trimesh, name: str) -> None:
    # GLB/glTF coordinates are conventionally meters; convert our mm fixtures to meters.
    mesh.apply_scale(0.001)
    mesh.export(OUT / name)


def main() -> None:
    cylinder = trimesh.creation.cylinder(radius=RADIUS_MM, height=LENGTH_MM, sections=512)
    # Cylinder is Z-axis aligned and centered at z=0. Save as GLB in meters.
    export(cylinder.copy(), "known_cylinder_120mm_200mm_circumference.glb")

    wrong_scale = cylinder.copy()
    wrong_scale.apply_scale(10.0)
    export(wrong_scale, "wrong_scale_10x.glb")

    # A cylindrical side surface only: remove cap faces by selecting faces whose
    # triangles are not horizontal. This deliberately creates boundary edges.
    side_only = cylinder.copy()
    tri = side_only.triangles
    normals = side_only.face_normals
    keep = (abs(normals[:, 2]) < 0.5)
    broken = trimesh.Trimesh(vertices=side_only.vertices.copy(),
                             faces=side_only.faces[keep].copy(), process=False)
    export(broken, "broken_open_cylinder.glb")

    submitted = {
        "length_mm": LENGTH_MM,
        "stations": [
            {"name": "25%", "circumference_mm": CIRCUMFERENCE_MM, "point_location": "25% from proximal cut", "units": "mm"},
            {"name": "50%", "circumference_mm": CIRCUMFERENCE_MM, "point_location": "50% from proximal cut", "units": "mm"},
            {"name": "75%", "circumference_mm": CIRCUMFERENCE_MM, "point_location": "75% from proximal cut", "units": "mm"}
        ]
    }
    (OUT / "submitted_known.json").write_text(json.dumps(submitted, indent=2) + "\\n", encoding="utf-8")
    print(f"Created fixtures in {OUT.resolve()}")
    print(f"Expected nominal length={LENGTH_MM:.1f} mm; circumference={CIRCUMFERENCE_MM:.1f} mm.")
    print("Note: polygonal cylinder circumference is an approximation; increase sections for tighter agreement.")


if __name__ == "__main__":
    main()
