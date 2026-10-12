import numpy as np
import pytest
import trimesh

from epics_geometry.scale import scale_length


def test_uniform_scale_exports_metric_length(tmp_path):
    source = tmp_path / "box.glb"
    output = tmp_path / "box_scaled.glb"
    # Deliberately non-cubic: scaling one axis must not alter proportions.
    trimesh.creation.box(extents=(3.0, 2.0, 3.0)).export(source)

    report = scale_length(source, output, length_mm=76.2, axis="x", proximal_end="min")
    exported = trimesh.load_scene(output)

    assert report["status"] == "overall_length_scaled_only"
    assert report["exported_length_mm"] == pytest.approx(76.2, abs=0.01)
    assert exported.extents * 1000 == pytest.approx([76.2, 50.8, 76.2], abs=0.01)
    assert exported.bounds[0, 0] == pytest.approx(0.0, abs=1e-7)
    assert np.isfinite(exported.extents).all()
    assert output.with_suffix(".json").exists()


def test_rejects_invalid_measurement(tmp_path):
    source = tmp_path / "box.glb"
    trimesh.creation.box().export(source)
    with pytest.raises(ValueError, match="positive finite"):
        scale_length(source, tmp_path / "scaled.glb", length_mm=0, axis="x", proximal_end="min")
