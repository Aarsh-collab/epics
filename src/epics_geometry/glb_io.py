"""Static-GLB loading with scene transforms applied.

The GLB is rejected, rather than silently altered, when it contains content
the scaler cannot carry through deformation faithfully (animation, skins,
morph targets, non-triangle primitives, required extensions).
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import trimesh

from .errors import StubError

_GLB_MAGIC = 0x46546C67
_JSON_CHUNK = 0x4E4F534A
_TRIANGLES = 4
# Required extensions trimesh decodes faithfully. TRELLIS.2 writes WebP textures;
# they are re-encoded losslessly as PNG on export for wider importer support.
_READABLE_REQUIRED = {"EXT_texture_webp"}


def read_glb_json(path: Path) -> dict:
    """Return the JSON chunk of a binary glTF file."""
    data = Path(path).read_bytes()
    if len(data) < 20:
        raise StubError("unsupported_mesh", f"{path} is too small to be a GLB")
    magic, version, _length = struct.unpack_from("<III", data, 0)
    if magic != _GLB_MAGIC:
        raise StubError("unsupported_mesh", f"{path} is not a binary glTF (.glb) file")
    if version != 2:
        raise StubError("unsupported_mesh", f"GLB version {version} is not supported (need 2)")
    chunk_length, chunk_type = struct.unpack_from("<II", data, 12)
    if chunk_type != _JSON_CHUNK:
        raise StubError("unsupported_mesh", "GLB does not start with a JSON chunk")
    return json.loads(data[20 : 20 + chunk_length].decode("utf-8"))


def check_static_glb(doc: dict) -> list[str]:
    """Raise for unsupported glTF features; return non-fatal warnings."""
    problems = []
    if doc.get("animations"):
        problems.append(f"{len(doc['animations'])} animation(s)")
    if doc.get("skins"):
        problems.append("skinning")
    required = sorted(set(doc.get("extensionsRequired") or []) - _READABLE_REQUIRED)
    if required:
        problems.append(f"required extensions {required}")
    meshes = doc.get("meshes") or []
    if not meshes:
        problems.append("no meshes")
    for mi, mesh in enumerate(meshes):
        if mesh.get("weights"):
            problems.append(f"mesh {mi} has morph target weights")
        for pi, prim in enumerate(mesh.get("primitives", [])):
            if prim.get("targets"):
                problems.append(f"mesh {mi} primitive {pi} has morph targets")
            if prim.get("mode", _TRIANGLES) != _TRIANGLES:
                problems.append(f"mesh {mi} primitive {pi} is not a triangle list")
            if "KHR_draco_mesh_compression" in (prim.get("extensions") or {}):
                problems.append("Draco-compressed geometry")
    if problems:
        raise StubError(
            "unsupported_mesh",
            "GLB must be a static triangle mesh; found " + "; ".join(sorted(set(problems))),
        )
    warnings = []
    used = set(doc.get("extensionsUsed") or []) - _READABLE_REQUIRED
    if used:
        warnings.append(f"optional glTF extensions {sorted(used)} may not survive re-export")
    return warnings


@dataclass
class MeshInstance:
    """One scene-graph instance of a mesh, with its world transform baked in."""

    node: str
    geometry: str
    vertices: np.ndarray  # (n, 3) world coordinates, float64
    faces: np.ndarray  # (m, 3) int64, winding corrected for mirrored transforms
    visual: object  # trimesh visual carrying UVs/material, untouched


def load_instances(path: Path) -> tuple[list[MeshInstance], dict]:
    """Load a static GLB and bake every node transform into world coordinates."""
    doc = read_glb_json(path)
    warnings = check_static_glb(doc)
    # process=False keeps vertex order and UV/material seams exactly as stored.
    try:
        scene = trimesh.load_scene(path, file_type="glb", process=False)
    except Exception as exc:  # malformed buffers, unsupported images, ...
        raise StubError("unsupported_mesh", f"GLB could not be read: {exc}") from exc
    instances = []
    for node in scene.graph.nodes_geometry:
        transform, geom_name = scene.graph[node]
        geom = scene.geometry[geom_name]
        if not isinstance(geom, trimesh.Trimesh):
            raise StubError(
                "unsupported_mesh",
                f"node {node!r} holds {type(geom).__name__}, not a triangle mesh",
            )
        if len(geom.faces) == 0:
            continue
        v = np.asarray(geom.vertices, dtype=np.float64)
        world = v @ transform[:3, :3].T + transform[:3, 3]
        faces = np.asarray(geom.faces, dtype=np.int64)
        if np.linalg.det(transform[:3, :3]) < 0:
            faces = faces[:, ::-1].copy()
        instances.append(MeshInstance(node, geom_name, world, faces, geom.visual))
    if not instances:
        raise StubError("unsupported_mesh", "GLB contains no triangle geometry")
    info = {
        "nodes_with_geometry": len(instances),
        "materials": len(doc.get("materials") or []),
        "textures": len(doc.get("textures") or []),
        "images": len(doc.get("images") or []),
        "warnings": warnings,
    }
    return instances, info
