import json

import numpy as np
import pytest
import trimesh
from PIL import Image

from epics_geometry.pipeline import main, run
from epics_geometry.stub import measure_glb
from epics_geometry.synthetic import stub_meshes, stub_scene


def metres(meshes):
    for m in meshes:
        m.vertices = m.vertices / 1000.0
    return meshes


@pytest.fixture
def stub_glb(tmp_path):
    path = tmp_path / "generated.glb"
    stub_scene(metres(stub_meshes())).export(path)
    return path


def targets_for(glb, k=1.05):
    t = measure_glb(glb)
    return {
        "schema_version": 1, "units": "mm", "length_mm": t["length_mm"] * k,
        "sections": [{"fraction": s["fraction"], "circumference_mm": s["circumference_mm"] * k,
                      "max_width_mm": s["max_width_mm"] * k} for s in t["sections"]],
    }


def capture_dir(tmp_path, measurements):
    d = tmp_path / "capture"
    d.mkdir()
    Image.new("RGB", (8, 8)).save(d / "main.png")
    Image.new("RGB", (8, 8)).save(d / "side.png")
    capture = {
        "schema_version": "capture.v1",
        "photos": [{"role": "generation", "filename": "main.png"}, {"role": "review", "filename": "side.png"}],
        "stub_measurements": measurements,
    }
    (d / "capture.json").write_text(json.dumps(capture))
    return d


def test_capture_to_validated_mesh(tmp_path, stub_glb):
    out = tmp_path / "out"
    manifest = run(out, capture_dir=capture_dir(tmp_path, targets_for(stub_glb)), glb=stub_glb)
    assert manifest["status"] == "complete", manifest
    assert manifest["stages"]["scale"]["status"] == "measurements_matched"
    assert manifest["stages"]["validate"]["overall_status"] in {"pass", "warn"}
    assert manifest["review_status"] == "pending_human_review"
    assert (out / "scaled.glb").is_file() and (out / "validation.json").is_file()
    assert json.loads((out / "manifest.json").read_text())["status"] == "complete"
    assert len(manifest["input_photo"]["sha256"]) == 64


def test_incomplete_capture_stops_before_generation(tmp_path, stub_glb):
    out = tmp_path / "out"
    manifest = run(out, capture_dir=capture_dir(tmp_path, None))
    assert manifest["status"] == "error" and manifest["failed_stage"] == "inputs"
    assert "stub_measurements" in manifest["error"]["message"]
    assert "generate" not in manifest["stages"]


def test_impossible_measurements_rejected_up_front(tmp_path, stub_glb):
    bad = targets_for(stub_glb)
    bad["sections"][0]["circumference_mm"] = bad["sections"][0]["max_width_mm"] * 5
    manifest = run(tmp_path / "out", measurements=bad, glb=stub_glb)
    assert manifest["failed_stage"] == "inputs" and manifest["error"]["type"] == "MeasurementError"


def test_scale_failure_stops_without_result(tmp_path, stub_glb):
    outer = metres(stub_meshes(base="open"))[0]
    inner = metres(stub_meshes(a=30.0, b=26.0, length=117.0, base="open"))[0]
    path = tmp_path / "two_shells.glb"
    stub_scene([outer, inner]).export(path)
    manifest = run(tmp_path / "out", measurements=targets_for(stub_glb), glb=path)
    assert manifest["failed_stage"] == "scale"
    assert manifest["status"] == "unsupported_mesh"
    assert manifest["result_glb"] is None and "validate" not in manifest["stages"]
    assert not (tmp_path / "out" / "scaled.glb").exists()


def test_double_wall_hint(tmp_path, stub_glb, monkeypatch):
    # Real TRELLIS.2 output was one connected, double-walled surface.
    message = "cross-section at 50% has a nested loop: the mesh is a hollow, double-walled shell"
    monkeypatch.setattr("epics_geometry.pipeline.scale_stub",
                        lambda *a, **k: {"status": "section_unsupported", "message": message})
    manifest = run(tmp_path / "out", measurements=targets_for(stub_glb), glb=stub_glb)
    assert manifest["status"] == "section_unsupported"
    assert "--outer-shell" in manifest["hint"]


def test_refuses_non_empty_output(tmp_path, stub_glb):
    out = tmp_path / "out"
    out.mkdir()
    (out / "x").write_text("")
    with pytest.raises(FileExistsError):
        run(out, measurements=targets_for(stub_glb), glb=stub_glb)


def test_cli(tmp_path, stub_glb, capsys):
    m = tmp_path / "m.json"
    m.write_text(json.dumps(targets_for(stub_glb)))
    assert main(["--measurements", str(m), "--glb", str(stub_glb), "--out", str(tmp_path / "out")]) == 0
    assert "pending human review" in capsys.readouterr().out
