"""Extract the exterior surface of a specifically verified nested two-shell GLB.

This is an experimental preprocessing step, not a general background remover.
It refuses meshes unless exactly two substantial connected surfaces have nested
bounds and opposite signed volumes. The input file is never modified.

TRELLIS.2 output has been seen as a thin double-walled shell, which the stub
scaler rejects as ``section_unsupported``. Whether to measure only the outer
wall is a team decision, so the pipeline runs this only when asked
(``epics-pipeline run --outer-shell``) and records it in the manifest.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

from .glb_io import load_instances, read_glb_json
from .stub_geometry import build_topology


def extract(source: Path, target: Path) -> dict:
    source, target = Path(source), Path(target)
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists() or source.resolve() == target.resolve():
        raise FileExistsError(f"output already exists or equals input: {target}")

    instances, info = load_instances(source)
    if len(instances) != 1:
        raise ValueError("expected exactly one mesh instance")
    inst = instances[0]
    v, f = inst.vertices, inst.faces
    diagonal = float(np.linalg.norm(v.max(axis=0) - v.min(axis=0)))
    if diagonal <= 0 or not np.isfinite(v).all():
        raise ValueError("mesh coordinates must be finite and nonzero")

    # Merge coincident positions only for connectivity analysis. Keep original
    # vertices and UV seams when constructing the exported surface.
    keys = np.round(v / (diagonal * 1e-7)).astype(np.int64)
    _, first, vertex_to_merged = np.unique(
        keys, axis=0, return_index=True, return_inverse=True
    )
    merged = v[first]
    mf = vertex_to_merged[f]
    edges_a = np.concatenate((mf[:, 0], mf[:, 1], mf[:, 2]))
    edges_b = np.concatenate((mf[:, 1], mf[:, 2], mf[:, 0]))
    graph = coo_matrix(
        (np.ones(len(edges_a), dtype=np.uint8), (edges_a, edges_b)),
        shape=(len(merged), len(merged)),
    )
    count, labels = connected_components(graph, directed=False)
    if count != 2:
        raise ValueError(f"expected exactly two connected surfaces, found {count}")
    face_labels = labels[mf]
    if not np.all(face_labels == face_labels[:, :1]):
        raise ValueError("a triangle crosses component labels")
    face_labels = face_labels[:, 0]

    components = []
    for component_id in range(2):
        component_vertices = merged[labels == component_id]
        component_faces = mf[face_labels == component_id]
        triangles = merged[component_faces]
        cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        area = float(np.linalg.norm(cross, axis=1).sum() / 2)
        signed_volume = float(np.einsum("ij,ij->i", triangles[:, 0], cross).sum() / 6)
        components.append(
            {
                "id": component_id,
                "faces": len(component_faces),
                "area": area,
                "signed_volume": signed_volume,
                "bounds_min": component_vertices.min(axis=0),
                "bounds_max": component_vertices.max(axis=0),
            }
        )

    a, b = components
    def contains(outer: dict, inner: dict) -> bool:
        return bool(
            np.all(outer["bounds_min"] < inner["bounds_min"])
            and np.all(outer["bounds_max"] > inner["bounds_max"])
        )

    if contains(a, b):
        outer, inner = a, b
    elif contains(b, a):
        outer, inner = b, a
    else:
        raise ValueError("component bounds are not strictly nested; cannot identify an exterior")
    if not (outer["signed_volume"] > 0 > inner["signed_volume"]):
        raise ValueError("nested components do not have expected opposite orientations")
    volume_ratio = abs(inner["signed_volume"] / outer["signed_volume"])
    area_ratio = inner["area"] / outer["area"]
    if not (0.8 < volume_ratio < 1.0 and 0.8 < area_ratio < 1.0):
        raise ValueError("nested surfaces are not similar enough to be a thin double shell")

    kept_faces = f[face_labels == outer["id"]]
    used = np.unique(kept_faces)
    remap = np.full(len(v), -1, dtype=np.int64)
    remap[used] = np.arange(len(used))
    uv = getattr(inst.visual, "uv", None)
    if uv is None or len(uv) != len(v):
        raise ValueError("expected per-vertex UVs; refusing to drop texture mapping")
    visual = trimesh.visual.TextureVisuals(uv=uv[used], material=inst.visual.material)
    output_mesh = trimesh.Trimesh(
        vertices=v[used], faces=remap[kept_faces], visual=visual, process=False
    )
    scene = trimesh.Scene()
    scene.add_geometry(output_mesh, geom_name=inst.geometry, node_name=inst.node)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(scene.export(file_type="glb", include_normals=True))

    # Reopen the actual output and apply the production topology check.
    exported, exported_info = load_instances(target)
    build_topology(exported)
    out_visual = exported[0].visual
    out_uv = getattr(out_visual, "uv", None)
    if out_uv is None or out_uv.shape != uv[used].shape or not np.allclose(out_uv, uv[used], atol=1e-6):
        target.unlink(missing_ok=True)
        raise ValueError("UV coordinates changed during export")
    if exported_info["images"] < info["images"]:
        target.unlink(missing_ok=True)
        raise ValueError("texture images were lost during export")

    def summary(component: dict) -> dict:
        return {
            "id": component["id"],
            "faces": component["faces"],
            "area": component["area"],
            "signed_volume": component["signed_volume"],
            "bounds_min": component["bounds_min"].tolist(),
            "bounds_max": component["bounds_max"].tolist(),
        }

    return {
        "source": str(source),
        "output": str(target),
        "outer": summary(outer),
        "inner_removed": summary(inner),
        "volume_ratio_inner_to_outer": volume_ratio,
        "area_ratio_inner_to_outer": area_ratio,
        "output_images": len(read_glb_json(target).get("images") or []),
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="epics-outer-shell", description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    print(json.dumps(extract(args.source, args.target), indent=2))


if __name__ == "__main__":
    main()
