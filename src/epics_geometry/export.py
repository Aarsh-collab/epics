"""Export scaled reference geometry for CAD review."""
from pathlib import Path
import hashlib
import zipfile

import trimesh

from .glb_io import load_instances


def export_formats(scaled_glb: Path, out_dir: Path) -> dict:
    instances, _ = load_instances(scaled_glb)
    scene = trimesh.Scene()
    parts = []
    for i, instance in enumerate(instances):
        mesh = trimesh.Trimesh(vertices=instance.vertices * 1000, faces=instance.faces,
                               visual=instance.visual.copy(), process=False)
        scene.add_geometry(mesh, node_name=f"part_{i}", geom_name=f"part_{i}")
        parts.append(mesh)
    obj, textures = trimesh.exchange.obj.export_obj(scene, include_texture=True,
                                                     return_texture=True, mtl_name="scaled.mtl")
    archive = Path(out_dir) / "scaled_obj.zip"
    stl = Path(out_dir) / "scaled.stl"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("scaled.obj", obj)
        for name, data in textures.items():
            z.writestr(Path(name).name, data)
    geometry = trimesh.util.concatenate(parts)
    geometry.merge_vertices()
    stl.write_bytes(trimesh.exchange.stl.export_stl(geometry))
    return {"status": "complete", "units": "mm", "files": {
        path.name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path in (archive, stl)}}
