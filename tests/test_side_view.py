import json
import io
import hashlib

import numpy as np
import pytest
import trimesh
from PIL import Image, ImageDraw

from epics_geometry import side_view
from epics_geometry.photo_prep import png_bytes

POINTS = {"model_start": {"x": .2, "y": .5}, "stub_endpoint": {"x": .8, "y": .5}}


def test_photo(width=120):
    image = Image.new("RGB", (500, 280), (230, 230, 230))
    ImageDraw.Draw(image).rectangle((70, 140 - width / 2, 399, 139 + width / 2), fill=(50, 150, 80))
    return image


test_photo.__test__ = False


def mask(image):
    rgb = np.asarray(image, dtype=float)
    return (rgb[..., 1] > rgb[..., 0] + 30).astype(float)


def tube(flat=False):
    angles = np.linspace(0, np.pi, 65) if flat else np.linspace(0, 2 * np.pi, 128, endpoint=False)
    ring = np.stack([.02 * np.cos(angles), .02 * np.sin(angles)], axis=1)
    n = len(ring)
    vertices = np.vstack([np.c_[ring, np.zeros(n)], np.c_[ring, np.full(n, .1)],
                          [0, .005 if flat else 0, 0], [0, .005 if flat else 0, .1]])
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces.extend([(i, j, n + j), (i, n + j, n + i), (2 * n, j, i), (2 * n + 1, n + i, n + j)])
    mesh = trimesh.Trimesh(vertices, faces, process=True)
    mesh.fix_normals()
    return mesh


def capture(tmp_path):
    png_bytes_ = png_bytes(test_photo())
    (tmp_path / "main.png").write_bytes(png_bytes_)
    (tmp_path / "side.png").write_bytes(png_bytes_)
    return {"photos": [{"role": "generation", "filename": "main.png"}, {"role": "review", "filename": "side.png"}],
            "landmarks": {**POINTS, "side": POINTS}}


SCALE_REPORT = {"alignment": {"source_to_output_matrix_before_transverse_fit": np.eye(4).tolist()}}


@pytest.mark.parametrize("flat,status", [(False, "pass"), (True, "warn")])
def test_round_tube_passes_and_d_shape_with_same_top_outline_warns(tmp_path, flat, status):
    record = capture(tmp_path)
    mesh = tmp_path / "mesh.glb"
    tube(flat).export(mesh)
    before = mesh.read_bytes()
    report = side_view.evaluate_capture(tmp_path, record, mesh, mesh, SCALE_REPORT, mask_fn=mask)
    assert report["status"] == status, report
    assert mesh.read_bytes() == before
    assert len(report["mesh_width_over_length"]) == 19
    if flat:
        assert report["mean_relative_difference"] > .4
    else:
        assert report["max_relative_difference"] < .02
    json.dumps(report, allow_nan=False)


def test_profile_is_normalized_and_handles_rotated_exif_photo():
    image = test_photo()
    base = side_view.photo_profile(png_bytes(image), POINTS, mask)
    bigger = image.resize((1000, 560))
    large = side_view.photo_profile(png_bytes(bigger), POINTS, mask)
    assert np.allclose(base["width_over_length"], large["width_over_length"], atol=.003)
    exif = Image.Exif(); exif[274] = 6
    buf = io.BytesIO(); image.save(buf, format="JPEG", quality=100, exif=exif)
    rotated_points = {k: {"x": 1 - p["y"], "y": p["x"]} for k, p in POINTS.items()}
    rotated = side_view.photo_profile(buf.getvalue(), rotated_points, mask)
    assert np.allclose(base["width_over_length"], rotated["width_over_length"], atol=.01)


def test_ambiguous_main_view_is_not_resolved_using_the_side_photo(tmp_path):
    box = tmp_path / "box.glb"
    trimesh.creation.box(extents=[.04, .02, .1]).export(box)
    sections = side_view.mesh_sections(box)
    main = {"width_over_length": [.4] * 19}
    side = {"width_over_length": [.2] * 19}
    report = side_view.compare(main, side, sections, sections)
    assert report["status"] == "unavailable", report
    assert "ambiguous" in report["message"]
    assert len(report["orientation"]["candidate_main_angles_degrees"]) > 1


def test_source_orientation_and_scene_transform_are_preserved(tmp_path):
    record = capture(tmp_path)
    original, scaled = tmp_path / "original.glb", tmp_path / "scaled.glb"
    mesh = tube(True)
    transform = trimesh.transformations.rotation_matrix(.63, [1, .3, .7])
    transform[:3, 3] = [1, 2, 3]
    scene = trimesh.Scene(); scene.add_geometry(mesh, transform=transform); scene.export(original)
    mesh.export(scaled)
    report = side_view.evaluate_capture(tmp_path, record, original, scaled,
        {"alignment": {"source_to_output_matrix_before_transverse_fit": np.linalg.inv(transform).tolist()}}, mask_fn=mask)
    assert report["status"] == "warn", report


@pytest.mark.parametrize("missing", ["side_photo", "side_points", "main_points"])
def test_missing_inputs_are_unavailable_without_loading_mask_model(tmp_path, missing):
    record = capture(tmp_path)
    if missing == "side_photo": record["photos"].pop()
    elif missing == "side_points": del record["landmarks"]["side"]
    else: del record["landmarks"]["model_start"]
    def unexpected(image):
        raise AssertionError("must not load the mask model")
    result = side_view.evaluate_capture(tmp_path, record, tmp_path / "absent", tmp_path / "absent", SCALE_REPORT, mask_fn=unexpected)
    assert result["status"] == "unavailable"
    assert result.get("error_type") != "AssertionError"


def test_model_failure_and_clipped_photo_are_unavailable(tmp_path):
    record = capture(tmp_path)
    def fail(image):
        raise RuntimeError("Segmentation model unavailable")
    result = side_view.evaluate_capture(tmp_path, record, tmp_path / "unused", tmp_path / "unused", SCALE_REPORT, mask_fn=fail)
    assert result["status"] == "unavailable"
    assert "Segmentation model unavailable" in result["message"]
    clipped = test_photo(); ImageDraw.Draw(clipped).rectangle((380, 80, 499, 199), fill=(50, 150, 80))
    with pytest.raises(ValueError, match="edge"):
        side_view.photo_profile(png_bytes(clipped), POINTS, mask)


def test_main_profile_mismatch_is_unavailable(tmp_path):
    mesh = tmp_path / "mesh.glb"; tube().export(mesh)
    sections = side_view.mesh_sections(mesh)
    report = side_view.compare({"width_over_length": [.8] * 19}, {"width_over_length": [.4] * 19}, sections, sections)
    assert report["status"] == "unavailable"
    assert "viewing direction" in report["message"]


def test_side_path_cannot_leave_capture_folder(tmp_path):
    record = capture(tmp_path); record["photos"][1]["filename"] = "../outside.png"
    result = side_view.evaluate_capture(tmp_path, record, tmp_path / "unused", tmp_path / "unused", SCALE_REPORT, mask_fn=mask)
    assert result["status"] == "unavailable" and "inside the capture" in result["message"]


def test_prepared_main_mask_is_reused_and_integrity_checked(tmp_path):
    record = capture(tmp_path); record["preparation"] = {"id": "synthetic"}
    prep = tmp_path / "prep"; prep.mkdir()
    data = (tmp_path / "main.png").read_bytes()
    alpha_data = png_bytes(Image.fromarray((mask(test_photo()) * 255).astype(np.uint8)))
    (prep / "mask.png").write_bytes(alpha_data)
    (prep / "prep.json").write_text(json.dumps({
        "original_sha256": hashlib.sha256(data).hexdigest(),
        "landmarks_normalized": {"start": POINTS["model_start"], "tip": POINTS["stub_endpoint"]},
        "sha256": {"mask.png": hashlib.sha256(alpha_data).hexdigest()},
    }))
    calls = []
    def segment(image):
        calls.append(1); return mask(image)
    path = tmp_path / "mesh.glb"; tube().export(path)
    result = side_view.evaluate_capture(tmp_path, record, path, path, SCALE_REPORT, mask_fn=segment)
    assert result["status"] == "pass", result
    assert len(calls) == 1  # side only; main mask already exists
    (prep / "mask.png").write_bytes(b"changed")
    result = side_view.evaluate_capture(tmp_path, record, path, path, SCALE_REPORT, mask_fn=segment)
    assert result["status"] == "unavailable" and "changed" in result["message"]


def rounded_photo():
    yy, xx = np.mgrid[:280, :500]
    z = (xx + .5 - 100) / 3
    radius = 60 * np.sqrt(np.clip(1 - (np.maximum(z - 80, 0) / 20) ** 2, 0, 1))
    foreground = (xx >= 70) & (z < 100) & (np.abs(yy + .5 - 140) <= radius)
    rgb = np.full((280, 500, 3), 230, dtype=np.uint8); rgb[foreground] = [50, 150, 80]
    return Image.fromarray(rgb)


def test_full_pipeline_compares_a_round_stub_after_scaling(tmp_path):
    from epics_geometry.pipeline import run
    from epics_geometry.synthetic import stub_meshes, stub_scene
    from test_pipeline import metres, targets_for

    source = tmp_path / "source.glb"
    stub_scene(metres(stub_meshes(a=20, b=20, length=100, tip=20))).export(source)
    folder = tmp_path / "capture"; folder.mkdir()
    record = capture(folder)
    for name in ("main.png", "side.png"): rounded_photo().save(folder / name)
    record.update(schema_version="capture.v1", stub_measurements=targets_for(source))
    (folder / "capture.json").write_text(json.dumps(record))
    result = run(tmp_path / "out", capture_dir=folder, glb=source, side_mask_fn=mask)
    assert result["status"] == "complete", result
    assert result["stages"]["side_view"]["status"] == "pass", result["stages"]["side_view"]
    saved = json.loads((tmp_path / "out" / "validation.json").read_text())
    assert saved["side_view"] == result["stages"]["side_view"]
