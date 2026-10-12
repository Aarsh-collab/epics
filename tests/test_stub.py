import json
import math
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import numpy as np
import pytest
import trimesh

from epics_geometry.glb_io import load_instances, read_glb_json
from epics_geometry.measurements import MeasurementError, parse_measurements, station_distances_mm
from epics_geometry.stub import measure_glb, scale_stub
from epics_geometry.synthetic import stub_meshes, stub_scene

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "measurements.example.json"


def to_metres(meshes):
    out = []
    for m in meshes:
        m = m.copy()
        m.vertices = m.vertices / 1000.0
        out.append(m)
    return out


def write(meshes, path, transform=None):
    stub_scene(meshes, transform).export(path)
    return path


def measurements_of(path):
    """Ground-truth seven values, measured on an undistorted mesh."""
    t = measure_glb(path)
    return {
        "schema_version": 1,
        "units": "mm",
        "length_mm": t["length_mm"],
        "sections": [
            {"fraction": s["fraction"], "circumference_mm": s["circumference_mm"], "max_width_mm": s["max_width_mm"]}
            for s in t["sections"]
        ],
    }


def distort(meshes, sx=1.0, sy=1.0, taper=0.0, units=1.0):
    """Generator-like errors: wrong transverse proportions, wrong taper, arbitrary units."""
    out = []
    for m in meshes:
        m = m.copy()
        v = m.vertices.copy()
        k = 1.0 + taper * v[:, 2] / v[:, 2].max() if v[:, 2].max() > 0 else 1.0
        v[:, 0] *= sx * k
        v[:, 1] *= sy * k
        m.vertices = v * units
        out.append(m)
    return out


def random_pose(seed):
    rng = np.random.default_rng(seed)
    q = rng.normal(size=4)
    T = trimesh.transformations.quaternion_matrix(q / np.linalg.norm(q))
    T[:3, 3] = rng.uniform(-2, 2, size=3)
    return T


def assert_matched(report):
    assert report["status"] == "measurements_matched", (report["message"], report.get("details"))
    assert all(r["within_tolerance"] for r in report["results"])
    assert report["input"]["unchanged"]


# ------------------------------------------------------------------ measurement input


def test_example_measurements_parse_and_stations():
    m = parse_measurements(json.loads(EXAMPLE.read_text()))
    assert [s.fraction for s in m.sections] == [0.25, 0.5, 0.75]
    assert station_distances_mm(120) == pytest.approx([30, 60, 90])


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda d: d["sections"].pop(), "missing section"),
        (lambda d: d.update(schema_version=2), "schema_version"),
        (lambda d: d.update(units="cm"), "units"),
        (lambda d: d["sections"][0].update(fraction=0.3), "not one of"),
        (lambda d: d["sections"][1].update(circumference_mm=400), "exceeds pi"),
        (lambda d: d["sections"][1].update(circumference_mm=100), "twice the max width"),
        (lambda d: d.update(length_mm=-5), "positive"),
    ],
)
def test_rejects_bad_measurements(mutate, match):
    d = json.loads(EXAMPLE.read_text())
    mutate(d)
    with pytest.raises(MeasurementError, match=match):
        parse_measurements(d)


# ------------------------------------------------------------------ measuring


def test_measurement_matches_analytic_ellipse(tmp_path):
    a, b = 32.0, 24.0
    path = write(to_metres(stub_meshes(a=a, b=b, n_theta=512)), tmp_path / "e.glb")
    m = measure_glb(path)
    h = ((a - b) / (a + b)) ** 2
    ramanujan = math.pi * (a + b) * (1 + 3 * h / (10 + math.sqrt(4 - 3 * h)))
    assert m["length_mm"] == pytest.approx(120.0, abs=1e-3)
    for s in m["sections"]:
        assert s["circumference_mm"] == pytest.approx(ramanujan, rel=1e-4)
        assert s["max_width_mm"] == pytest.approx(2 * a, abs=1e-3)


# ------------------------------------------------------------------ recovery

SHAPES = {
    "circular": dict(a=30, b=30),
    "elliptical": dict(a=34, b=26),
    "tapered": dict(a=33, b=28, taper=0.25),
    "asymmetric": dict(a=31, b=27, asym=0.12, taper=0.1),
    "open_base": dict(a=32, b=27, base="open"),
    "short_wide": dict(length=70, a=36, b=33, tip=25),
}
DISTORTIONS = {
    "anisotropic": dict(sx=1.14, sy=0.91),
    "taper": dict(sx=0.95, sy=1.05, taper=0.2),
    "inflated": dict(sx=1.2, sy=1.15),
}


@pytest.mark.parametrize("shape", SHAPES)
@pytest.mark.parametrize("dist", DISTORTIONS)
def test_recovers_known_measurements(tmp_path, shape, dist):
    truth = stub_meshes(**SHAPES[shape])
    target = measurements_of(write(to_metres(truth), tmp_path / "truth.glb"))
    seed = zlib.crc32(f"{shape}-{dist}".encode())
    source = write(distort(truth, units=0.0083, **DISTORTIONS[dist]), tmp_path / "in.glb", random_pose(seed))

    report = scale_stub(source, target, tmp_path / "out.glb")
    assert_matched(report)

    # An independent re-read, as the inspection step does.
    check = measure_glb(tmp_path / "out.glb")
    assert check["length_mm"] == pytest.approx(target["length_mm"], abs=0.5)
    for got, want in zip(check["sections"], target["sections"]):
        assert got["circumference_mm"] == pytest.approx(want["circumference_mm"], abs=max(0.5, 0.01 * want["circumference_mm"]))
        assert got["max_width_mm"] == pytest.approx(want["max_width_mm"], abs=max(0.5, 0.01 * want["max_width_mm"]))


def test_scene_transforms_including_mirror_are_applied(tmp_path):
    truth = stub_meshes(a=33, b=27)
    target = measurements_of(write(to_metres(truth), tmp_path / "truth.glb"))
    T = random_pose(7) @ np.diag([2.5, 2.5, -2.5, 1.0])  # scaled and mirrored node
    rotated = distort(truth, sx=1.1, units=0.004)
    pre = random_pose(3)
    for m in rotated:
        m.apply_transform(pre)  # rotation baked into vertices, plus node transform
    source = write(rotated, tmp_path / "in.glb", T)
    assert_matched(scale_stub(source, target, tmp_path / "out.glb"))


def test_export_is_metres_in_canonical_frame_with_textures(tmp_path):
    truth = stub_meshes(a=32, b=28)
    target = measurements_of(write(to_metres(truth), tmp_path / "truth.glb"))
    source = write(distort(truth, sx=1.1, units=0.01), tmp_path / "in.glb", random_pose(11))
    before = source.read_bytes()
    report = scale_stub(source, target, tmp_path / "out.glb")
    assert_matched(report)
    assert source.read_bytes() == before

    scene = trimesh.load_scene(tmp_path / "out.glb", process=False)
    lo, hi = scene.bounds
    assert lo[2] == pytest.approx(0.0, abs=1e-6)
    assert hi[2] - lo[2] == pytest.approx(target["length_mm"] / 1000.0, abs=5e-4)
    assert hi[2] < 1.0  # metres, not millimetres

    doc = read_glb_json(tmp_path / "out.glb")
    assert len(doc["images"]) == len(read_glb_json(source)["images"]) == 2
    src, _ = load_instances(source)
    out, _ = load_instances(tmp_path / "out.glb")
    for s, o in zip(sorted(src, key=lambda i: i.node), sorted(out, key=lambda i: i.node)):
        assert np.allclose(s.visual.uv, o.visual.uv, atol=1e-6)
        assert len(s.vertices) == len(o.vertices)
        assert np.array_equal(s.faces, o.faces)
    assert report["verification"]["uv_coordinates_preserved"]


def test_transverse_factors_are_smooth_and_bounded(tmp_path):
    truth = stub_meshes(a=32, b=28)
    target = measurements_of(write(to_metres(truth), tmp_path / "truth.glb"))
    # Make the stations need different corrections.
    target["sections"][0]["circumference_mm"] *= 1.08
    target["sections"][0]["max_width_mm"] *= 1.08
    target["sections"][2]["circumference_mm"] *= 0.93
    target["sections"][2]["max_width_mm"] *= 0.93
    source = write(to_metres(truth), tmp_path / "in.glb")
    report = scale_stub(source, target, tmp_path / "out.glb")
    assert_matched(report)

    station = np.array([[s["factor_x"], s["factor_y"]] for s in report["deformation"]["stations"]])
    prof = report["deformation"]["factor_profile_sampled"]
    z, f = np.array(prof["z_mm"]), np.array(prof["factors"])
    assert (f >= station.min(0) - 1e-6).all() and (f <= station.max(0) + 1e-6).all()  # no overshoot
    assert np.allclose(f[z <= 30], station[0], atol=1e-5)  # held toward base
    assert np.allclose(f[z >= 90], station[2], atol=1e-5)  # held toward tip
    assert (np.diff(f[:, 0]) <= 1e-9).all()  # monotone between monotone stations

    # Exported ring radii vary smoothly along the length.
    out, _ = load_instances(tmp_path / "out.glb")
    body = next(i for i in out if i.node == "node_0")
    ring = body.vertices[: 90 * 129].reshape(90, 129, 3)
    radius = np.linalg.norm(ring[:, :-1, :2] - ring[:, :-1, :2].mean(1, keepdims=True), axis=2).mean(1)
    assert np.abs(np.diff(radius, 2)).max() < 0.05 * 1e-3 * 30  # metres


# ------------------------------------------------------------------ rejections


def run(tmp_path, meshes, target=None, **kw):
    source = write(meshes, tmp_path / "in.glb")
    if target is None:
        target = json.loads(EXAMPLE.read_text())
    report = scale_stub(source, target, tmp_path / "out.glb", **kw)
    assert not (tmp_path / "out.glb").exists()
    assert not list(tmp_path.glob(".*partial*"))
    assert json.loads((tmp_path / "out.report.json").read_text())["status"] == report["status"]
    return report


def test_rejects_two_flat_ends(tmp_path):
    cyl = trimesh.creation.cylinder(radius=0.03, height=0.12, sections=96)
    assert run(tmp_path, [cyl])["status"] == "alignment_ambiguous"


def test_rejects_no_flat_end(tmp_path):
    cap = trimesh.creation.capsule(radius=0.03, height=0.09, count=[64, 64])
    assert run(tmp_path, [cap])["status"] == "alignment_ambiguous"


def test_rejects_curved_axis(tmp_path):
    report = run(tmp_path, to_metres(stub_meshes(bend=0.35)))
    assert report["status"] == "alignment_ambiguous"
    assert "bend" in report["message"] or "lean" in report["message"]


def test_rejects_open_measurement_loop(tmp_path):
    body, cap = stub_meshes()
    c = body.triangles_center
    keep = ~((np.abs(c[:, 2] - 60) < 4) & (c[:, 0] > 20))
    body = trimesh.Trimesh(body.vertices, body.faces[keep], visual=body.visual, process=False)
    report = run(tmp_path, to_metres([body, cap]))
    assert report["status"] == "section_unsupported"
    assert "not a closed loop" in report["message"]


def test_rejects_concave_section(tmp_path):
    report = run(tmp_path, to_metres(stub_meshes(indent=0.45)))
    assert report["status"] == "section_unsupported"
    assert "concave" in report["message"]


def test_rejects_contradictory_measurements(tmp_path):
    target = json.loads(EXAMPLE.read_text())
    target["sections"][1]["circumference_mm"] = 3.5 * target["sections"][1]["max_width_mm"]
    report = run(tmp_path, to_metres(stub_meshes()), target)
    assert report["status"] == "invalid_measurements"


def test_rejects_excessive_deformation(tmp_path):
    truth = stub_meshes(a=30, b=30)
    target = measurements_of(write(to_metres(truth), tmp_path / "truth.glb"))
    for s in target["sections"]:
        s["circumference_mm"] *= 1.8
        s["max_width_mm"] *= 1.8
    (tmp_path / "truth.glb").unlink()
    report = run(tmp_path, to_metres(truth), target)
    assert report["status"] == "fit_failed"
    assert "outside the allowed" in report["message"]


def test_rejects_background_geometry(tmp_path):
    floor = trimesh.creation.box(extents=(0.3, 0.3, 0.01))
    floor.apply_translation((0, 0, -0.05))
    assert run(tmp_path, to_metres(stub_meshes()) + [floor])["status"] == "unsupported_mesh"


def test_rejects_animation(tmp_path):
    source = write(to_metres(stub_meshes()), tmp_path / "in.glb")
    data = source.read_bytes()
    (json_len,) = struct.unpack_from("<I", data, 12)
    doc = json.loads(data[20 : 20 + json_len])
    doc["animations"] = [{"channels": [], "samplers": []}]
    chunk = json.dumps(doc).encode()
    chunk += b" " * (-len(chunk) % 4)
    rest = data[20 + json_len :]
    body = struct.pack("<II", len(chunk), 0x4E4F534A) + chunk + rest
    source.write_bytes(struct.pack("<III", 0x46546C67, 2, 12 + len(body)) + body)
    report = scale_stub(source, json.loads(EXAMPLE.read_text()), tmp_path / "out.glb")
    assert report["status"] == "unsupported_mesh"
    assert "animation" in report["message"]


def test_refuses_to_overwrite(tmp_path):
    source = write(to_metres(stub_meshes()), tmp_path / "in.glb")
    (tmp_path / "out.glb").write_bytes(b"keep me")
    with pytest.raises(FileExistsError):
        scale_stub(source, json.loads(EXAMPLE.read_text()), tmp_path / "out.glb")
    assert (tmp_path / "out.glb").read_bytes() == b"keep me"
    with pytest.raises(ValueError):
        scale_stub(source, json.loads(EXAMPLE.read_text()), source, overwrite=True)


# ------------------------------------------------------------------ command line


def cli(*args):
    return subprocess.run([sys.executable, "-c", "import sys; from epics_geometry.stub import main, measure_main; "
                           f"sys.exit({args[0]}(sys.argv[1:]))", *args[1:]], capture_output=True, text=True)


def test_command_line_scale_and_measure(tmp_path):
    truth = stub_meshes(a=31, b=29, taper=0.15)
    target = measurements_of(write(to_metres(truth), tmp_path / "truth.glb"))
    (tmp_path / "m.json").write_text(json.dumps(target))
    source = write(distort(truth, sx=1.1, units=0.006), tmp_path / "in.glb", random_pose(5))

    r = cli("main", str(source), "--measurements", str(tmp_path / "m.json"), "--output", str(tmp_path / "s.glb"))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "measurements_matched" in r.stdout
    assert (tmp_path / "s.report.json").exists()

    again = cli("main", str(source), "--measurements", str(tmp_path / "m.json"), "--output", str(tmp_path / "s.glb"))
    assert again.returncode == 2 and "exists" in again.stderr

    m = cli("measure_main", str(tmp_path / "s.glb"))
    assert m.returncode == 0
    assert json.loads(m.stdout)["length_mm"] == pytest.approx(target["length_mm"], abs=0.5)


def test_length_only_command_still_works(tmp_path):
    source = tmp_path / "box.glb"
    trimesh.creation.box(extents=(3.0, 2.0, 3.0)).export(source)
    r = subprocess.run(
        [sys.executable, "-m", "epics_geometry.scale", str(source), str(tmp_path / "box_scaled.glb"),
         "--length-mm", "50", "--axis", "x", "--proximal-end", "min"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["exported_length_mm"] == pytest.approx(50, abs=0.01)


def test_accepts_webp_textures_like_trellis(tmp_path):
    truth = stub_meshes(a=32, b=27)
    target = measurements_of(write(to_metres(truth), tmp_path / "truth.glb"))
    source = tmp_path / "in.glb"
    source.write_bytes(stub_scene(distort(truth, sy=1.1, units=0.005)).export(file_type="glb", extension_webp=True))
    assert "EXT_texture_webp" in read_glb_json(source)["extensionsRequired"]
    report = scale_stub(source, target, tmp_path / "out.glb")
    assert_matched(report)
    assert report["verification"]["texture_images"] == 2


def test_section_loop_diagnostics_distinguish_shells_from_branches():
    from epics_geometry.errors import StubError
    from epics_geometry.stub_geometry import Slice, main_loop

    t = np.linspace(0, 2 * np.pi, 64, endpoint=False)
    circle = np.stack([np.cos(t), np.sin(t)], axis=1)
    with pytest.raises(StubError, match="inner wall"):
        main_loop(Slice([30 * circle, 29 * circle], 0), "here")
    with pytest.raises(StubError, match="side-by-side"):
        main_loop(Slice([30 * circle, 8 * circle + [60, 0]], 0), "here")
    loop, ignored = main_loop(Slice([30 * circle, 0.5 * circle + [60, 0]], 0), "here")
    assert len(loop) == 64 and ignored == 1


# ------------------------------------------------------------------ output paths


def matched_inputs(tmp_path):
    truth = stub_meshes(a=32, b=28)
    target = measurements_of(write(to_metres(truth), tmp_path / "truth.glb"))
    source = write(distort(truth, sx=1.1, units=0.01), tmp_path / "in.glb", random_pose(2))
    return source, target


@pytest.mark.parametrize("overwrite", [False, True])
def test_report_path_may_not_equal_output(tmp_path, overwrite):
    source, target = matched_inputs(tmp_path)
    out = tmp_path / "out.glb"
    with pytest.raises(ValueError, match=r"\.json"):
        scale_stub(source, target, out, report_path=out, overwrite=overwrite)
    with pytest.raises(ValueError, match="must all differ"):
        scale_stub(source, target, source, report_path=tmp_path / "r.json", overwrite=overwrite)
    out.write_bytes(b"previous mesh")
    with pytest.raises(ValueError, match=r"\.json"):
        scale_stub(source, target, out, report_path=out, overwrite=overwrite)
    assert out.read_bytes() == b"previous mesh"  # rejected before touching anything


def test_success_writes_valid_glb_and_separate_report(tmp_path):
    source, target = matched_inputs(tmp_path)
    out, rep = tmp_path / "out.glb", tmp_path / "out.report.json"
    report = scale_stub(source, target, out)
    assert_matched(report)
    assert out.read_bytes()[:4] == b"glTF" and rep.read_bytes()[:1] == b"{"
    saved = json.loads(rep.read_text())
    assert saved["status"] == "measurements_matched"
    assert saved["output"]["glb"] == str(out)
    assert saved["output"]["sha256"] == __import__("hashlib").sha256(out.read_bytes()).hexdigest()
    assert measure_glb(out)["length_mm"] == pytest.approx(target["length_mm"], abs=0.5)
    assert not list(tmp_path.glob(".*partial*"))


def test_overwrite_failure_leaves_no_stale_mesh(tmp_path):
    source, target = matched_inputs(tmp_path)
    out, rep = tmp_path / "out.glb", tmp_path / "out.report.json"
    assert_matched(scale_stub(source, target, out))
    bad = json.loads(json.dumps(target))
    for s in bad["sections"]:
        s["circumference_mm"] *= 1.8
        s["max_width_mm"] *= 1.8
    report = scale_stub(source, bad, out, overwrite=True)
    assert report["status"] == "fit_failed"
    assert not out.exists()  # the earlier successful mesh is gone, not mistaken for this run
    assert json.loads(rep.read_text())["status"] == "fit_failed"
    assert json.loads(rep.read_text())["output"]["glb"] is None


def test_overwrite_success_replaces_both_files(tmp_path):
    source, target = matched_inputs(tmp_path)
    out, rep = tmp_path / "out.glb", tmp_path / "out.report.json"
    out.write_bytes(b"old mesh")
    rep.write_text('{"status": "measurements_matched", "old": true}')
    report = scale_stub(source, target, out, overwrite=True)
    assert_matched(report)
    saved = json.loads(rep.read_text())
    assert "old" not in saved and saved["created_utc"] == report["created_utc"]
    assert out.read_bytes()[:4] == b"glTF"


def test_command_line_rejects_report_equal_to_output(tmp_path):
    source, target = matched_inputs(tmp_path)
    (tmp_path / "m.json").write_text(json.dumps(target))
    r = cli("main", str(source), "--measurements", str(tmp_path / "m.json"),
            "--output", str(tmp_path / "s.glb"), "--report", str(tmp_path / "s.glb"), "--overwrite")
    assert r.returncode == 2 and ".json" in r.stderr
    assert not (tmp_path / "s.glb").exists()
