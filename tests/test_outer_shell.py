import numpy as np
import pytest
import trimesh
from PIL import Image

from epics_geometry.glb_io import load_instances
from epics_geometry.outer_shell import CONTACT_GAP, ShellError, extract
from epics_geometry.stub import measure_glb
from epics_geometry.synthetic import stub_meshes

TEXTURE = Image.fromarray(np.full((16, 16, 3), (180, 120, 90), dtype=np.uint8))
SIZE = dict(n_theta=48, n_body=40, n_tip=12)


def part(mesh: trimesh.Trimesh, uv=None):
    uv = mesh.visual.uv if uv is None else uv
    return np.asarray(mesh.vertices) / 1000.0, np.asarray(mesh.faces), np.asarray(uv)


def arm(flip=False, shift=0.0, **kw):
    """One closed arm surface (body + base cap) in mm, as a list of parts."""
    body, cap = stub_meshes(**{**SIZE, **kw})
    out = []
    for m in (body, cap):
        v, f, uv = part(m)
        out.append((v + [0, 0, shift / 1000.0], f[:, ::-1] if flip else f, uv))
    return out


def sphere(centre_mm, radius_mm, pole=None):
    s = trimesh.creation.uv_sphere(radius=radius_mm, count=[16, 16])
    if pole is not None:  # turn the +z pole to point along ``pole``
        s.apply_transform(trimesh.geometry.align_vectors([0, 0, 1], pole))
    s.apply_translation(centre_mm)
    uv = np.full((len(s.vertices), 2), 0.5)
    return part(s, uv)


def write(path, parts):
    vs, fs, uvs, offset = [], [], [], 0
    for v, f, uv in parts:
        vs.append(v)
        fs.append(f + offset)
        uvs.append(uv)
        offset += len(v)
    mesh = trimesh.Trimesh(np.concatenate(vs), np.concatenate(fs), process=False,
                           visual=trimesh.visual.TextureVisuals(uv=np.concatenate(uvs), image=TEXTURE))
    trimesh.Scene(mesh).export(path)
    return path


def run(tmp_path, parts, **kw):
    src = write(tmp_path / "generated.glb", parts)
    return src, extract(src, tmp_path / "clean.glb", tmp_path / "review.glb", **kw)


def closed(path):
    v, f = load_instances(path)[0][0].vertices, load_instances(path)[0][0].faces
    m = trimesh.Trimesh(v, f)  # merges UV seams by position
    m.merge_vertices(merge_tex=True, merge_norm=True)
    return m.is_watertight


def decisions(report):
    return {p["piece"]: p["decision"] for p in report["pieces"]}


def test_nested_double_wall_keeps_outer(tmp_path):
    inner = arm(flip=True, shift=1.5, a=30.5, b=26.5, length=117.0, tip=28.5)
    src, report = run(tmp_path, arm() + inner)
    assert report["status"] == "cleaned"
    assert report["walls"] == {**report["walls"], "outer": 1, "inner": 2}
    assert report["walls"]["inner_checks"]["inside_fraction"] == 1.0
    assert report["walls"]["inner_checks"]["visible_fraction"] == 0.0
    assert decisions(report)[2] == "removed as the inner wall"
    single = write(tmp_path / "single.glb", arm())
    measured = [{k: val for k, val in measure_glb(g).items() if k != "glb"} for g in (tmp_path / "clean.glb", single)]
    assert measured[0] == measured[1]  # flat base, tip and sections unchanged
    assert (tmp_path / "review.glb").is_file()


def test_single_wall_continues(tmp_path):
    src, report = run(tmp_path, arm())
    assert report["status"] == "cleaned" and report["walls"]["inner"] is None
    assert report["removed_pieces"] == [] and report["fused_pieces"] == []
    assert report["output_faces"] == len(load_instances(src)[0][0].faces)
    out = load_instances(tmp_path / "clean.glb")[0][0]
    assert np.allclose(np.sort(out.vertices, axis=0), np.sort(load_instances(src)[0][0].vertices, axis=0))


def test_hidden_internal_fragment_removed(tmp_path):
    _, report = run(tmp_path, arm() + [sphere([0, 0, 60], 5)])
    piece = report["pieces"][-1]
    assert piece["decision"] == "removed: hidden inside the outer wall"
    assert piece["inside_fraction"] == 1.0 and piece["visible_fraction"] == 0.0
    assert report["review_flags"] == []


def test_detached_visible_piece_removed_and_flagged(tmp_path):
    _, report = run(tmp_path, arm() + [sphere([50, 0, 60], 5)])
    piece = report["pieces"][-1]
    assert piece["decision"] == "removed: separate from the outer wall"
    assert piece["gap"] > CONTACT_GAP * report["diagonal"]
    assert report["review_flags"] == [piece["piece"]]


def test_touching_tip_piece_is_welded(tmp_path):
    body, cap = arm()
    v, f, uv = body
    tip = np.all(v[f][:, :, 2] > 0.1, axis=1)  # tip cap faces, past the last full ring
    diagonal = np.linalg.norm(np.ptp(np.concatenate([body[0], cap[0]]), axis=0))
    ids = np.unique(f[tip])
    remap = np.full(len(v), -1)
    remap[ids] = np.arange(len(ids))
    nudge = [0, 0, 0.4 * CONTACT_GAP * diagonal]
    tip_part = (v[ids] + nudge, remap[f[tip]], uv[ids])
    _, report = run(tmp_path, [(v, f[~tip], uv), cap, tip_part])
    assert report["status"] == "cleaned"
    assert report["repairs"]["welded_pieces"] == [2]
    assert report["pieces"][1]["decision"].startswith("fused: welded")
    assert closed(tmp_path / "clean.glb")
    out = load_instances(tmp_path / "clean.glb")[0][0].vertices
    # Only the welded ring snaps onto the wall; the rest of the tip keeps its own positions.
    assert out[:, 2].max() == pytest.approx(v[:, 2].max() + nudge[2], abs=1e-9)
    assert report["pieces"][1]["max_weld_shift"] <= CONTACT_GAP * diagonal


def test_touching_closed_piece_bridges_tiny_gap(tmp_path):
    parts = arm()
    diagonal = np.linalg.norm(np.ptp(np.concatenate([p[0] for p in parts]), axis=0)) * 1000
    ring_z = parts[0][0][48 * 2 + 2, 2] * 1000  # a body ring, so x=32 mm is a vertex
    gap = 0.5 * CONTACT_GAP * diagonal
    _, report = run(tmp_path, parts + [sphere([32 + 4 + gap, 0, ring_z], 4, pole=[-1, 0, 0])])
    assert report["status"] == "cleaned", report.get("reason")
    assert report["pieces"][-1]["decision"].startswith("fuse")
    assert report["fused_pieces"] == [report["pieces"][-1]["piece"]]
    assert 0 < report["repairs"]["fused"][0]["gap_bridged_by_shift"] < 3 * CONTACT_GAP * diagonal / 1000
    assert closed(tmp_path / "clean.glb")


def test_intersecting_piece_is_fused_with_texture(tmp_path):
    src, report = run(tmp_path, arm() + [sphere([31.5, 0.7, 60.3], 6)])
    piece = report["pieces"][-1]
    assert piece["crossing_edges"] > 0 and piece["decision"].startswith("fuse")
    assert report["texture"]["new_vertices"] > 0
    assert report["texture"]["mean_colour_difference"] < 5
    out = load_instances(tmp_path / "clean.glb")[0][0]
    assert out.vertices[:, 0].max() > 0.032 + 0.005  # the bump is part of the exterior now
    assert closed(tmp_path / "clean.glb")


def test_small_hole_filled_locally(tmp_path):
    (v, f, uv), cap = arm()
    quads = (SIZE["n_body"] + SIZE["n_tip"] - 1) * SIZE["n_theta"]
    keep = np.ones(len(f), bool)
    keep[[20 * 48 + 5, quads + 20 * 48 + 5]] = False  # both triangles of one body quad
    body = (v, f[keep], uv)
    src, report = run(tmp_path, [body, cap])
    holes = report["repairs"]["outer_wall"]["holes"]
    assert len(holes) >= 1 and all(h["filled"] for h in holes)
    assert closed(tmp_path / "clean.glb")
    assert report["shape"]["end_shift_from_repair"] == [0.0, 0.0]


def test_hole_across_uv_seam_uses_flat_colour(tmp_path):
    (v, f, uv), cap = arm()
    seam = np.where(np.isclose(uv[f][:, :, 0], 1.0).any(axis=1))[0]
    keep = np.ones(len(f), bool)
    keep[seam[10]] = False
    src, report = run(tmp_path, [(v, f[keep], uv), cap])
    hole = report["repairs"]["outer_wall"]["holes"][0]
    assert hole["edges"] == 3 and hole["texture"].startswith("flat colour")
    assert closed(tmp_path / "clean.glb")


def test_large_hole_refused_with_original_kept(tmp_path):
    body, _ = arm()  # open base: a 48-edge hole
    with pytest.raises(ShellError, match="local-repair limit") as info:
        run(tmp_path, [body, sphere([0, 0, 60], 5)])
    assert info.value.report["status"] == "refused"
    assert not (tmp_path / "clean.glb").exists()
    assert (tmp_path / "generated.glb").is_file() and (tmp_path / "review.glb").is_file()


def test_ambiguous_second_surface_refused(tmp_path):
    other = [(v + [0.1, 0, 0], f, uv) for v, f, uv in arm()]
    with pytest.raises(ShellError, match="inner-wall checks") as info:
        run(tmp_path, arm() + other)
    assert info.value.report["walls"]["inner_checks"]["inside_fraction"] == 0.0
    assert not (tmp_path / "clean.glb").exists()


def test_three_large_surfaces_refused(tmp_path):
    inner = arm(flip=True, shift=1.5, a=30.5, b=26.5, length=117.0, tip=28.5)
    other = [(v + [0.1, 0, 0], f, uv) for v, f, uv in arm()]
    with pytest.raises(ShellError, match="3 large surfaces"):
        run(tmp_path, arm() + inner + other)
