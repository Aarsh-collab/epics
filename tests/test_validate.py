import json
import math

import numpy as np
import pytest
import trimesh

from epics_geometry.stub import measure_glb, scale_stub
from epics_geometry.synthetic import stub_meshes, stub_scene
from epics_geometry.validate import _axis_vector, evaluate, main, normalize_submitted

LENGTH_MM = 120.0
CIRCUMFERENCE_MM = 200.0
SUBMITTED = {
    "length_mm": LENGTH_MM,
    "stations": [{"name": f"{p}%", "circumference_mm": CIRCUMFERENCE_MM} for p in (25, 50, 75)],
}


def cylinder_glb(path, scale=1.0, open_sides=False, transform=None):
    """Z-aligned cylinder exported in metres, as glTF expects."""
    c = trimesh.creation.cylinder(radius=CIRCUMFERENCE_MM / (2 * math.pi), height=LENGTH_MM, sections=512)
    if open_sides:
        keep = np.abs(c.face_normals[:, 2]) < 0.5
        c = trimesh.Trimesh(vertices=c.vertices, faces=c.faces[keep], process=False)
    c.apply_scale(scale / 1000.0)
    if transform is not None:
        c.apply_transform(transform)
    c.export(path)
    return path


def test_known_cylinder_passes(tmp_path):
    report = evaluate(cylinder_glb(tmp_path / "c.glb"), SUBMITTED, axis_mode="fixed", axis="z")
    assert report["overall_status"] == "pass"
    assert report["length"]["measured_mm"] == pytest.approx(LENGTH_MM, abs=1e-3)
    for s in report["stations"]:
        assert s["measured_mm"] == pytest.approx(CIRCUMFERENCE_MM, abs=0.1)


def test_wrong_scale_fails(tmp_path):
    report = evaluate(cylinder_glb(tmp_path / "c.glb", scale=10.0), SUBMITTED, axis_mode="fixed", axis="z")
    assert report["overall_status"] == "fail"
    assert report["length"]["status"] == "fail"


def test_open_cylinder_warns_not_watertight(tmp_path):
    report = evaluate(cylinder_glb(tmp_path / "c.glb", open_sides=True), SUBMITTED, axis_mode="fixed", axis="z")
    assert report["overall_status"] == "warn"
    assert not report["checks"]["mesh_defects"]["is_watertight"]


def test_axis_y_uses_y(tmp_path):
    # Regression: "y" used to map to the X component.
    assert list(_axis_vector("y")) == [0, 1, 0]
    assert list(_axis_vector("-x")) == [-1, 0, 0]
    to_y = trimesh.transformations.rotation_matrix(-math.pi / 2, [1, 0, 0])
    report = evaluate(cylinder_glb(tmp_path / "c.glb", transform=to_y), SUBMITTED, axis_mode="fixed", axis="y")
    assert report["length"]["measured_mm"] == pytest.approx(LENGTH_MM, abs=1e-3)


def test_pca_finds_long_axis(tmp_path):
    report = evaluate(cylinder_glb(tmp_path / "c.glb"), SUBMITTED)
    assert abs(report["checks"]["orientation"]["axis_vector_xyz"][2]) == pytest.approx(1.0, abs=1e-6)


def test_unmeasurable_station_not_verified(tmp_path):
    submitted = json.loads(json.dumps(SUBMITTED))
    submitted["stations"][2] = {"name": "75%", "circumference_mm": None, "unmeasurable": True}
    report = evaluate(cylinder_glb(tmp_path / "c.glb"), submitted, axis_mode="fixed", axis="z")
    assert report["stations"][2]["status"] == "not_verified"
    assert report["overall_status"] == "warn"


def test_normalizes_stub_and_capture_formats():
    stub = {"schema_version": 1, "units": "mm", "length_mm": 120,
            "sections": [{"fraction": 0.25, "circumference_mm": 190, "max_width_mm": 64}]}
    assert normalize_submitted(stub) == {"length_mm": 120, "stations": [{"name": "25%", "circumference_mm": 190}]}
    capture = {"schema_version": "capture.v1", "length": {"value": 120},
               "sections": [{"fraction": 0.5, "value": None, "reliability": "unable_to_measure"}]}
    out = normalize_submitted(capture)
    assert out["length_mm"] == 120 and out["stations"][0]["name"] == "50%" and out["stations"][0]["unmeasurable"]


def test_validates_scaler_output_in_canonical_frame(tmp_path):
    """The scaler's exported mesh agrees with an independently built check."""
    src = tmp_path / "stub.glb"
    meshes = stub_meshes()
    for m in meshes:
        m.vertices = m.vertices / 1000.0
    stub_scene(meshes).export(src)
    t = measure_glb(src)
    target = {
        "schema_version": 1, "units": "mm", "length_mm": t["length_mm"] * 1.1,
        "sections": [{"fraction": s["fraction"], "circumference_mm": s["circumference_mm"] * 1.05,
                      "max_width_mm": s["max_width_mm"] * 1.05} for s in t["sections"]],
    }
    out = tmp_path / "scaled.glb"
    assert scale_stub(src, target, out)["status"] == "measurements_matched"
    report = evaluate(out, target, axis_mode="canonical")
    assert report["length"]["status"] == "ok"
    assert [s["status"] for s in report["stations"]] == ["ok", "ok", "ok"]


def test_cli_writes_valid_json(tmp_path):
    glb = cylinder_glb(tmp_path / "c.glb")
    submitted = tmp_path / "m.json"
    submitted.write_text(json.dumps(SUBMITTED))
    out = tmp_path / "report.json"
    assert main([str(glb), str(submitted), "--axis-mode", "fixed", "--axis", "z", "-o", str(out)]) == 0
    assert json.loads(out.read_text())["overall_status"] == "pass"
