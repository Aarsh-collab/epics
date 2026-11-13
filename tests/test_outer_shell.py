import numpy as np
import pytest
import trimesh
from PIL import Image

from epics_geometry.glb_io import load_instances
from epics_geometry.outer_shell import CONTACT_GAP, ShellError, extract
from epics_geometry.stub import measure_glb, scale_stub
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


@pytest.mark.parametrize("shared_edge", [False, True])
def test_joined_rim_keeps_outer_geometry(tmp_path, shared_edge):
    body = arm()[0]
    v, f, uv = body
    inner = (0.97 * v, f[:, ::-1], uv)
    n = SIZE["n_theta"]
    ring = np.vstack([v[:n], inner[0][:n]])
    i = np.arange(n)
    j = (i + 1) % n
    annulus = np.vstack([np.column_stack([j, i, n+i]), np.column_stack([j, n+i, n+j])])
    if shared_edge:
        inner[0][:n+1] = v[:n+1]
        parts = [body, inner]
    else:
        parts = [body, inner, (ring, annulus, np.zeros((2*n, 2)))]
    _, report = run(tmp_path, parts)
    assert report["repairs"]["outer_wall"]["joined_rim"]["cap_faces"] == n - 2
    assert closed(tmp_path / "clean.glb")
    expected = write(tmp_path / "expected.glb", arm())
    assert measure_glb(expected)["length_mm"] == pytest.approx(measure_glb(tmp_path / "clean.glb")["length_mm"])


def test_detached_fragment_excluded_from_measurements(tmp_path):
    single = write(tmp_path / "single.glb", arm())
    stray = write(tmp_path / "stray.glb", arm() + [sphere([100, 0, 185], 2.5)])
    assert measure_glb(stray)["length_mm"] == pytest.approx(measure_glb(single)["length_mm"])
    truth = measure_glb(single)
    target = {"schema_version": 1, "units": "mm", "length_mm": truth["length_mm"],
              "sections": [{k: row[k] for k in ("fraction", "circumference_mm", "max_width_mm")}
                           for row in truth["sections"]]}
    report = scale_stub(stray, target, tmp_path / "scaled.glb")
    assert report["status"] == "measurements_matched", report
    assert any("detached fragment" in w for w in report["warnings"])


@pytest.mark.parametrize("shared_edge", [False, True])
@pytest.mark.parametrize("roughness,tilt,scale", [
    (0.0003, 0, 0.97), (0, 5, 0.97), (0, 15, 0.97),
    (0, 0, 0.92), (0.0003, 15, 0.92),
])
def test_joined_rim_uneven_tilted_thick(tmp_path, shared_edge, roughness, tilt, scale):
    v, f, uv = arm()[0]
    v = v.copy()
    n = SIZE["n_theta"]
    inner_v = scale * v
    if shared_edge:
        inner_v[:n+1] = v[:n+1]
    # Apply the same shear to both walls: the actual cut plane tilts while
    # the long limb axis remains nearly unchanged.
    noise = np.random.default_rng(7).uniform(-roughness, roughness, n)
    for points in (v, inner_v):
        points[:, 2] += points[:, 0] * np.tan(np.deg2rad(tilt))
        points[:n, 2] += noise
        points[n, 2] += noise[0]  # duplicate UV seam vertex
    parts = [(v, f, uv), (inner_v, f[:, ::-1], uv)]
    if not shared_edge:
        i = np.arange(n)
        j = (i+1) % n
        annulus = np.vstack((np.column_stack((j, i, n+i)),
                             np.column_stack((j, n+i, n+j))))
        parts.append((np.vstack((v[:n], inner_v[:n])), annulus, np.zeros((2*n, 2))))
    _, report = run(tmp_path, parts)
    split = report["repairs"]["outer_wall"]["joined_rim"]
    assert split["inner_faces_removed"] == len(f)
    assert report["kept_original_outer_faces"] == len(f)
    assert closed(tmp_path / "clean.glb")


@pytest.mark.parametrize("scale,shift", [(0.5, 0), (0.92, 0.02)])
def test_abandoned_joined_split_does_not_mutate_surface(scale, shift):
    from epics_geometry.outer_shell import _Surface, _split_joined_rim
    v, f, uv = arm()[0]
    # Reject either excessive thickness or an inner wall protruding sideways.
    n = SIZE["n_theta"]
    inner = scale * v
    inner[:, 0] += shift * np.sin(np.pi * v[:, 2] / v[:, 2].max())
    inner[:n+1] = v[:n+1]
    vertices = np.vstack((v, inner))
    faces = np.vstack((f, f[:, ::-1] + len(v)))
    diagonal = np.linalg.norm(np.ptp(vertices, axis=0))
    surface = _Surface(vertices, np.vstack((uv, uv)), diagonal)
    before = [x.copy() for x in (surface.v, surface.uv, surface.mid, surface.mv)]
    assert _split_joined_rim(surface, faces, diagonal) is None
    for actual, expected in zip((surface.v, surface.uv, surface.mid, surface.mv), before):
        np.testing.assert_array_equal(actual, expected)


@pytest.mark.parametrize("scale,disable_split", [(0.97, True), (0.5, False)])
def test_unsplittable_joined_wall_is_refused(tmp_path, monkeypatch, scale, disable_split):
    import epics_geometry.outer_shell as shell
    v, f, uv = arm()[0]
    inner = scale * v
    inner[:SIZE["n_theta"]+1] = v[:SIZE["n_theta"]+1]
    if disable_split:
        monkeypatch.setattr(shell, "_split_joined_rim", lambda *args: None)
    with pytest.raises(ShellError, match="hidden surface"):
        run(tmp_path, [(v, f, uv), (inner, f[:, ::-1], uv)])
    assert not (tmp_path / "clean.glb").exists()


def test_concave_rim_cap_follows_loop():
    import manifold3d
    from epics_geometry.outer_shell import _Surface, _rim_cap, _manifold
    # U-shaped cross-section: its centroid lies in the missing notch.
    polygon = np.array([[0, 0], [3, 0], [3, 3], [2, 3],
                        [2, 1], [1, 1], [1, 3], [0, 3]], dtype=float)
    n = len(polygon)
    vertices = np.vstack((np.c_[polygon, np.zeros(n)], np.c_[polygon, np.full(n, 10)]))
    i = np.arange(n)
    j = (i + 1) % n
    sides = np.vstack((np.column_stack((i, j, n+j)), np.column_stack((i, n+j, n+i))))
    top = n + np.asarray(manifold3d.triangulate([polygon]))
    wall = np.vstack((sides, top))
    diagonal = np.linalg.norm(np.ptp(vertices, axis=0))
    surface = _Surface(vertices, np.zeros((2*n, 2)), diagonal)
    capped = _rim_cap(surface, wall, diagonal, np.array([0, 0, 1]), 0)
    solid = _manifold(surface, capped)
    assert solid.volume() == pytest.approx(70)
    np.testing.assert_array_equal(surface.v[:len(vertices)], vertices)
    assert len(capped) - len(wall) == n - 2


def joined_rim_case(*, join, bumps_mm=0, tilt_deg=0, shape="oval",
                    gap=0.03, uneven=False, fragments=False):
    """Joined walls in metres; only the first ring gets bumps/tilt.

    Return the input parts and the original exterior body, without a base cap.
    Shared joins include the duplicated UV seam vertex. Bridged joins use a
    two-triangle-wide annulus, not disconnected overlapping walls.
    """
    v, f, uv = arm(a=32, b=24 if shape == "oval" else 28)[0]
    v = v.copy()
    n = SIZE["n_theta"]
    if shape == "d":
        # Vary the flat side along the limb: a D cross-section without
        # a second long planar face that the scaler mistakes for a base.
        limit = -0.014 * (1 + 0.3 * np.sin(np.pi * v[:, 2] / v[:, 2].max()))
        v[:, 0] = np.maximum(v[:, 0], limit)
    theta = np.arctan2(v[:, 1], v[:, 0])
    gaps = gap + (0.06 * (1 + np.cos(theta)) if uneven else np.zeros(len(v)))
    inner = v * (1 - gaps[:, None])
    noise = np.random.default_rng(19).normal(0, bumps_mm / 1000, n)
    for points in (v, inner):
        points[:n, 2] += noise + points[:n, 0] * np.tan(np.deg2rad(tilt_deg))
        points[n] = points[0]  # same position, different UV
    if join == "shared":
        inner[:n+1] = v[:n+1]
    exterior = (v, f, uv)
    parts = [exterior, (inner, f[:, ::-1], uv)]
    if join == "band":
        i = np.arange(n)
        j = (i + 1) % n
        faces = np.vstack((np.column_stack((j, i, n+i)),
                           np.column_stack((j, n+i, n+j))))
        parts.append((np.vstack((v[:n], inner[:n])), faces, np.zeros((2*n, 2))))
    if fragments:
        parts.append(sphere([0, 0, 55], 2))
    return parts, exterior


# Deliberately small matrix: isolated stressors plus combinations, both joins.
MESSY_RIMS = [
    pytest.param({}, id="flat-3pct"),
    pytest.param(dict(gap=0.08), id="flat-8pct"),
    pytest.param(dict(gap=0.15), id="flat-15pct"),
    pytest.param(dict(bumps_mm=0.3), id="bumps-0.3mm"),
    pytest.param(dict(bumps_mm=1), id="bumps-1mm"),
    pytest.param(dict(tilt_deg=5), id="rim-tilt-5deg"),
    pytest.param(dict(tilt_deg=15), id="rim-tilt-15deg"),
    pytest.param(dict(shape="d"), id="d-section"),
    pytest.param(dict(uneven=True), id="uneven-3-to-15pct"),
    pytest.param(dict(fragments=True), id="hidden-fragment"),
    pytest.param(dict(bumps_mm=0.3, tilt_deg=5, uneven=True,
                      fragments=True), id="combined-oval"),
    pytest.param(dict(shape="d", bumps_mm=1, tilt_deg=15,
                      uneven=True, fragments=True), id="combined-d"),
]


def triangle_keys(vertices, faces):
    # GLB positions are float32; ignore UV duplication, face order and winding.
    xyz = np.round(np.asarray(vertices, dtype=np.float32).astype(float), 8)
    return {tuple(sorted(map(tuple, tri))) for tri in xyz[faces]}


@pytest.mark.parametrize("join", ["shared", "band"])
@pytest.mark.parametrize("settings", MESSY_RIMS)
def test_messy_joined_rim_splits_or_refuses(tmp_path, join, settings, record_property):
    parts, (v, f, uv) = joined_rim_case(join=join, **settings)
    source = write(tmp_path / "generated.glb", parts)
    original_bytes = source.read_bytes()
    original = load_instances(source)[0][0]
    topology = trimesh.Trimesh(original.vertices, original.faces)
    topology.merge_vertices(merge_tex=True, merge_norm=True)
    assert topology.is_watertight and topology.is_winding_consistent
    assert len(topology.split()) == 1 + int(settings.get("fragments", False))
    target = tmp_path / "clean.glb"
    try:
        report = extract(source, target, tmp_path / "review.glb")
    except ShellError as exc:
        assert exc.report["status"] == "refused"
        assert str(exc)
        assert not target.exists()
        record_property("outcome", "refused: " + str(exc))
    else:
        assert report["status"] == "cleaned"
        split = report["repairs"]["outer_wall"]["joined_rim"]
        assert split["inner_faces_removed"] == len(f)
        assert report["kept_original_outer_faces"] == len(f)
        out = load_instances(target)[0][0]
        expected_faces = triangle_keys(v, f)
        actual_faces = triangle_keys(out.vertices, out.faces)
        assert len(actual_faces) == len(out.faces)  # no duplicate/stacked faces
        assert expected_faces <= actual_faces
        # Only a local base cap may be added; no inner-wall/fragments survive.
        extra = actual_faces - expected_faces
        rim = {tuple(p) for p in np.round(v[:SIZE["n_theta"]].astype(np.float32).astype(float), 8)}
        assert extra and all(set(triangle) <= rim for triangle in extra)
        mesh = trimesh.Trimesh(out.vertices, out.faces)
        mesh.merge_vertices(merge_tex=True, merge_norm=True)
        assert mesh.is_watertight and mesh.is_winding_consistent
        assert len(mesh.split(only_watertight=False)) == 1
        # A reference cap closes the original rim, preserving its geometry.
        import manifold3d
        cap_faces = np.asarray(manifold3d.triangulate([v[:SIZE["n_theta"], :2]]))[:, ::-1]
        reference = write(tmp_path / "outer.glb", [(v, np.vstack((f, cap_faces)), uv)])
        assert measure_glb(target)["length_mm"] == pytest.approx(
            measure_glb(reference)["length_mm"], rel=1e-6, abs=1e-5)
        record_property("outcome", "split")
    assert source.read_bytes() == original_bytes


@pytest.mark.parametrize("indent", [0.35, 0.65], ids=["crease", "deep-crease"])
def test_single_wall_deep_crease_is_not_refused(tmp_path, indent):
    parts = arm(indent=indent)
    source, report = run(tmp_path, parts)
    assert report["status"] == "cleaned"
    assert report["walls"]["inner"] is None
    assert "joined_rim" not in report["repairs"]["outer_wall"]
    assert report["walls"]["hidden_area_fraction"] <= 0.10
    assert closed(tmp_path / "clean.glb")
    before = load_instances(source)[0][0]
    after = load_instances(tmp_path / "clean.glb")[0][0]
    assert triangle_keys(before.vertices, before.faces) == triangle_keys(after.vertices, after.faces)
