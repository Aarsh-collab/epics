import zipfile

import numpy as np
import trimesh

from epics_geometry.export import export_formats
from test_server import stub_glb, client, login, submit, capture, targets_for, wait


def test_exports_mm_geometry_and_texture(stub_glb, tmp_path):
    result = export_formats(stub_glb, tmp_path)
    assert result["units"] == "mm"
    stl = trimesh.load_mesh(tmp_path / "scaled.stl")
    assert stl.is_watertight
    assert abs(stl.extents[2] - 120) < .01
    with zipfile.ZipFile(tmp_path / "scaled_obj.zip") as archive:
        names = archive.namelist()
        assert "scaled.obj" in names and "scaled.mtl" in names
        assert any(name.endswith(".png") for name in names)
        assert b"map_Kd" in archive.read("scaled.mtl")
        archive.extractall(tmp_path / "obj")
    scene = trimesh.load_scene(tmp_path / "obj" / "scaled.obj", process=False)
    assert np.allclose(scene.extents, stl.extents, atol=.01)
    assert all(getattr(mesh.visual, "uv", None) is not None for mesh in scene.geometry.values())


def test_server_lists_and_serves_exports(client, stub_glb):
    login(client)
    response = submit(client, capture(targets_for(stub_glb)))
    job = wait(client, response.json()["id"])
    assert job["result"]["status"] == "complete"
    for name in ("scaled.stl", "scaled_obj.zip"):
        assert name in job["files"]
        assert client.get(f"/api/jobs/{job['id']}/files/{name}").status_code == 200


def test_export_failure_preserves_glb(client, stub_glb, monkeypatch):
    def fail(*args):
        raise RuntimeError("test failure")
    monkeypatch.setattr("epics_geometry.export.export_formats", fail)
    login(client)
    response = submit(client, capture(targets_for(stub_glb)))
    job = wait(client, response.json()["id"])
    assert job["result"]["status"] == "complete"
    assert "scaled.glb" in job["files"]
    manifest = client.get(f"/api/jobs/{job['id']}/files/manifest.json").json()
    assert manifest["stages"]["export"]["status"] == "warning"
