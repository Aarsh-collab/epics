"""Keep the exterior surface of a generated GLB and repair small defects.

This is an experimental preprocessing step run before the stub scaler when the
outer-wall option is selected (``epics-pipeline run --outer-shell``); whether to
measure only the outer wall is a team decision recorded in the manifest. The
input file is never modified. Every connected piece gets a recorded decision:

* The largest piece is the outer-wall candidate. A second piece of similar size
  is removed as an inner wall only if sampled points on it lie inside the outer
  wall, are hidden from every outside direction, and the two surfaces are close
  in area and volume. A single wall continues on its own.
* A smaller piece is removed when it is hidden inside the outer wall, fused
  when it touches or crosses the outer wall and can be seen from outside, and
  removed when it is separate (gap above ``CONTACT_GAP`` of the bounding-box
  diagonal). A removed but visible piece is flagged for review.
* Small holes are filled and back-to-back duplicate triangles are removed
  locally; nothing else on the surface moves unless a piece is fused, which
  uses a manifold3d union that carries the UVs through.

Anything the checks cannot support stops the run with a ``ShellError`` whose
``report`` explains why; the original mesh stays available for inspection.
The result is reference geometry that still needs human review.
"""

from __future__ import annotations

import argparse
import json
from itertools import product
from pathlib import Path

import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

from .glb_io import load_instances, read_glb_json
from .stub_geometry import build_topology

WALL_SHARE = 0.2  # a piece with this share of the largest piece's area is a wall
CONTACT_GAP = 1e-4  # fraction of the bounding-box diagonal: 0.01%
MAX_HOLE_EDGES = 32
MAX_HOLE_PERIMETER = 0.1  # fraction of the diagonal
MAX_MALFORMED_FACES = 64  # faces that may be cut out around non-manifold edges
INNER_INSIDE_MIN = 0.99
INNER_VISIBLE_MAX = 0.01
SHELL_RATIO = (0.8, 1.0)  # inner/outer area and volume ratios of a thin double shell
MAX_SAMPLES = 3000
VISIBILITY_SAMPLES = 300  # inner-wall points also ray-tested from outside, as a second check
MAX_COLOUR_DIFF = 40.0  # mean 0-255 RGB difference allowed at repaired vertices
MAX_LENGTH_CHANGE = 0.02  # fraction of the outer wall's length
# Unit vectors to the faces, edges and corners of a cube.
DIRECTIONS = np.array([d for d in product((-1, 0, 1), repeat=3) if any(d)], dtype=float)
DIRECTIONS /= np.linalg.norm(DIRECTIONS, axis=1, keepdims=True)
PARITY_DIRECTIONS = np.array([[0.267, 0.534, 0.802], [-0.873, 0.218, 0.436], [0.408, -0.816, -0.408]])


class ShellError(ValueError):
    """Cleanup refused; ``report`` holds the analysis made before stopping."""

    def __init__(self, message: str, report: dict | None = None):
        super().__init__(message)
        self.report = report or {}


class _Surface:
    """Faces over original vertices (UVs) plus position-merged ids (topology)."""

    def __init__(self, v: np.ndarray, uv: np.ndarray, diagonal: float):
        keys = np.round(v / (diagonal * 1e-7)).astype(np.int64)
        _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
        self.v, self.uv = v.copy(), uv.copy()
        self.mid = inverse.ravel()
        self.mv = v[first]

    def add_vertex(self, position, uv, mid: int | None = None) -> int:
        if mid is None:
            mid = len(self.mv)
            self.mv = np.vstack([self.mv, position])
        self.v = np.vstack([self.v, position])
        self.uv = np.vstack([self.uv, uv])
        self.mid = np.append(self.mid, mid)
        return len(self.v) - 1


def _undirected(mf: np.ndarray):
    e = np.sort(np.concatenate([mf[:, [0, 1]], mf[:, [1, 2]], mf[:, [2, 0]]]), axis=1)
    return np.unique(e, axis=0, return_inverse=True, return_counts=True)


def _edge_stats(mf: np.ndarray) -> dict:
    _, _, counts = _undirected(mf)
    return {"boundary_edges": int((counts == 1).sum()), "nonmanifold_edges": int((counts > 2).sum())}


def _area_volume(mv: np.ndarray, mf: np.ndarray) -> tuple[float, float]:
    tri = mv[mf]
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    return float(np.linalg.norm(cross, axis=1).sum() / 2), float(np.einsum("ij,ij->", tri[:, 0], cross) / 6)


def _remove_fins(mf: np.ndarray) -> tuple[np.ndarray, int, int]:
    """Drop back-to-back duplicate triangles and repeated copies of a triangle.

    Returns (kept face mask, back-to-back faces removed, repeated copies removed).
    """
    keep = np.ones(len(mf), dtype=bool)
    # Rotate each face so its smallest id comes first; the order of the other two gives the winding.
    rolled = np.take_along_axis(mf, (np.argmin(mf, axis=1)[:, None] + np.arange(3)) % 3, axis=1)
    flipped = rolled[:, 1] > rolled[:, 2]
    key = np.sort(mf, axis=1)
    _, group, counts = np.unique(key, axis=0, return_inverse=True, return_counts=True)
    back_to_back = repeats = 0
    for g in np.where(counts > 1)[0]:
        members = np.where(group.ravel() == g)[0]
        if flipped[members].any() and not flipped[members].all():
            keep[members] = False
            back_to_back += len(members)
        else:
            keep[members[1:]] = False
            repeats += len(members) - 1
    return keep, back_to_back, repeats


def _boundary_loops(mf: np.ndarray, faces: np.ndarray) -> tuple[list[list[int]], dict[int, int]]:
    """Closed loops of boundary vertices, ordered for filling with outward faces.

    Also returns, for each loop vertex, the original vertex (UV) used by the
    face that owns the open edge leaving it in fill order.
    """
    half = np.concatenate([mf[:, [0, 1]], mf[:, [1, 2]], mf[:, [2, 0]]])
    half_orig = np.concatenate([faces[:, [0, 1]], faces[:, [1, 2]], faces[:, [2, 0]]])
    _, inverse, counts = _undirected(mf)
    open_edge = counts[inverse] == 1
    nxt: dict[int, int] = {}
    corner: dict[int, int] = {}
    for (a, b), (_, ob) in zip(half[open_edge], half_orig[open_edge]):
        if b in nxt:
            raise ShellError("a boundary vertex is shared by two holes (bow-tie); cannot fill it locally")
        nxt[int(b)] = int(a)  # the filling face runs the edge backwards
        corner[int(b)] = int(ob)
    loops, seen = [], set()
    for start in nxt:
        if start in seen:
            continue
        loop, cur = [], start
        while cur not in seen:
            seen.add(cur)
            loop.append(cur)
            cur = nxt.get(cur)
            if cur is None:
                raise ShellError("a boundary edge chain does not close into a loop")
        if cur != start:
            raise ShellError("boundary edges form a figure-eight; cannot fill it locally")
        loops.append(loop)
    return loops, corner


def _uv_scale(s: _Surface, faces: np.ndarray) -> float:
    uv = s.uv[faces]
    return float(np.median(np.linalg.norm(uv[:, 1] - uv[:, 0], axis=1))) or 1e-3


def _fill_holes(s: _Surface, faces: np.ndarray, diagonal: float) -> tuple[np.ndarray, list[dict]]:
    """Fill every boundary loop of ``faces`` (original-vertex ids); return new faces and a log."""
    loops, corner = _boundary_loops(s.mid[faces], faces)
    if not loops:
        return np.empty((0, 3), dtype=np.int64), []
    uv_limit = 20 * _uv_scale(s, faces)
    new_faces, log = [], []
    for loop in loops:
        pts = s.mv[loop]
        perimeter = float(np.linalg.norm(pts - np.roll(pts, -1, axis=0), axis=1).sum())
        entry = {"edges": len(loop), "perimeter": perimeter, "centre": pts.mean(axis=0).tolist()}
        if len(loop) > MAX_HOLE_EDGES or perimeter > MAX_HOLE_PERIMETER * diagonal:
            entry["filled"] = False
            log.append(entry)
            raise ShellError(
                f"hole with {len(loop)} edges and perimeter {perimeter / diagonal:.2%} of the diagonal "
                f"is above the local-repair limit ({MAX_HOLE_EDGES} edges, {MAX_HOLE_PERIMETER:.0%})",
                {"holes": log},
            )
        ids = [corner[m] for m in loop]
        uvs = s.uv[ids]
        if np.linalg.norm(uvs - uvs.mean(axis=0), axis=1).max() <= uv_limit:
            entry["texture"] = "interpolated from the hole's edge"
        else:  # the loop crosses a UV seam: colour the patch from one edge vertex
            ids = [s.add_vertex(s.v[i], uvs[0], s.mid[i]) for i in ids]
            uvs = np.repeat(uvs[:1], len(ids), axis=0)
            entry["texture"] = "flat colour from an edge vertex (hole crosses a UV seam)"
        if len(loop) == 3:
            new_faces.append(ids)
        else:
            centre = s.add_vertex(s.mv[loop].mean(axis=0), uvs.mean(axis=0))
            new_faces.extend([ids[i], ids[(i + 1) % len(ids)], centre] for i in range(len(ids)))
        entry["filled"] = True
        entry["new_faces"] = 1 if len(loop) == 3 else len(loop)
        log.append(entry)
    return np.asarray(new_faces, dtype=np.int64), log


def _repair(s: _Surface, faces: np.ndarray, diagonal: float) -> tuple[np.ndarray, np.ndarray, dict]:
    """Remove fins and malformed spots, fill small holes. Returns (faces, patch mask, log)."""
    keep, back_to_back, repeats = _remove_fins(s.mid[faces])
    faces = faces[keep]
    log = {"back_to_back_faces_removed": back_to_back, "repeated_faces_removed": repeats,
           "malformed_faces_removed": 0, "holes": []}
    _, inverse, counts = _undirected(s.mid[faces])
    bad = counts[inverse.reshape(3, -1).T] > 2
    bad_faces = bad.any(axis=1)
    if bad_faces.any():
        if bad_faces.sum() > MAX_MALFORMED_FACES:
            raise ShellError(f"{int(bad_faces.sum())} faces touch non-manifold edges; too many to repair locally",
                             {"repairs": log})
        faces = faces[~bad_faces]
        log["malformed_faces_removed"] = int(bad_faces.sum())
    try:
        patch, log["holes"] = _fill_holes(s, faces, diagonal)
    except ShellError as exc:
        exc.report = {"repairs": {**log, **exc.report}}
        raise
    merged = np.concatenate([faces, patch])
    stats = _edge_stats(s.mid[merged])
    if stats["boundary_edges"] or stats["nonmanifold_edges"]:
        raise ShellError(f"surface still has {stats['boundary_edges']} open and {stats['nonmanifold_edges']} "
                         "non-manifold edges after local repair", {"repairs": log})
    mask = np.r_[np.zeros(len(faces), bool), np.ones(len(patch), bool)]
    return merged, mask, log


def _manifold(s: _Surface, faces: np.ndarray):
    import manifold3d

    used, local = np.unique(faces, return_inverse=True)
    mid = s.mid[used]
    _, first, inverse = np.unique(mid, return_index=True, return_inverse=True)
    canon = first[inverse.ravel()]
    moved = np.where(canon != np.arange(len(used)))[0]
    mesh = manifold3d.Mesh(
        vert_properties=np.ascontiguousarray(np.c_[s.v[used], s.uv[used]], dtype=np.float32),
        tri_verts=np.ascontiguousarray(local.reshape(-1, 3), dtype=np.uint32),
        merge_from_vert=moved.astype(np.uint32), merge_to_vert=canon[moved].astype(np.uint32),
    )
    solid = manifold3d.Manifold(mesh)
    if solid.status() != manifold3d.Error.NoError:
        raise ShellError(f"repaired surface is not a valid closed solid ({solid.status().name})")
    return solid


def _ray_hits(solid, start: np.ndarray, end: np.ndarray, skip: float) -> int:
    return sum(h.distance > skip for h in solid.ray_cast(tuple(start), tuple(end)))


def _inside(solid, points: np.ndarray, diagonal: float) -> np.ndarray:
    """Majority vote of three ray-parity tests per point."""
    votes = np.zeros(len(points), dtype=int)
    reach = 3 * diagonal
    for d in PARITY_DIRECTIONS:
        d = d / np.linalg.norm(d)
        votes += [_ray_hits(solid, p, p + d * reach, 1e-9) % 2 for p in points]
    return votes >= 2


def _visible(solid, points: np.ndarray, diagonal: float, candidates: np.ndarray | None = None) -> np.ndarray:
    """Whether each point can be seen from outside along any of 26 directions.

    Only ``candidates`` are tested; a point inside the closed outer wall cannot
    be seen, so callers pass the points found outside it.
    """
    reach, lift = 1.5 * diagonal, 1e-6 * diagonal
    seen = np.zeros(len(points), dtype=bool)
    pending = np.ones(len(points), dtype=bool) if candidates is None else candidates.copy()
    for d in DIRECTIONS:
        for i in np.where(pending & ~seen)[0]:
            p = points[i] + d * lift
            if not solid.ray_cast(tuple(p), tuple(p + d * reach)):
                seen[i] = True
    return seen


def _distance(points: np.ndarray, mv: np.ndarray, mf: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Exact distance from each point to the triangle surface (mv, mf).

    Returns (distance, closest point, triangle index). Only triangles whose
    centroid is within the nearest-vertex distance plus the largest
    centroid-to-corner radius can be closer than that vertex.
    """
    tri = mv[mf]
    centroids = tri.mean(axis=1)
    radius = float(np.linalg.norm(tri - centroids[:, None], axis=2).max())
    d0, _ = cKDTree(mv[np.unique(mf)]).query(points)
    tree = cKDTree(centroids)
    dist, closest, index = np.empty(len(points)), np.empty((len(points), 3)), np.empty(len(points), dtype=int)
    for i, (p, r) in enumerate(zip(points, d0)):
        cand = np.asarray(tree.query_ball_point(p, r + radius), dtype=int)
        q = trimesh.triangles.closest_point(tri[cand], np.repeat(p[None], len(cand), axis=0))
        k = int(np.argmin(np.linalg.norm(q - p, axis=1)))
        dist[i], closest[i], index[i] = np.linalg.norm(q[k] - p), q[k], cand[k]
    return dist, closest, index


def _crossing_edges(solid, mv: np.ndarray, mf: np.ndarray) -> int:
    e = np.unique(np.sort(np.concatenate([mf[:, [0, 1]], mf[:, [1, 2]], mf[:, [2, 0]]]), axis=1), axis=0)
    return sum(bool(solid.ray_cast(tuple(mv[a]), tuple(mv[b]))) for a, b in e)


def _open_vertices(mf: np.ndarray) -> np.ndarray:
    e, _, counts = _undirected(mf)
    return np.unique(e[counts == 1])


def _sample(mv: np.ndarray, mf: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    pts = np.concatenate([mv[np.unique(mf)], mv[mf].mean(axis=1)])
    if len(pts) > MAX_SAMPLES:
        pts = pts[rng.choice(len(pts), MAX_SAMPLES, replace=False)]
    return pts


def _axis_extent(points: np.ndarray, centre: np.ndarray, axis: np.ndarray) -> tuple[float, float]:
    t = (points - centre) @ axis
    return float(t.min()), float(t.max())


def _colours(visual, uv: np.ndarray) -> np.ndarray | None:
    image = getattr(visual.material, "baseColorTexture", None)
    if image is None:
        return None
    return trimesh.visual.color.uv_to_color(uv, image)[:, :3].astype(float)


def _write(target: Path, v: np.ndarray, faces: np.ndarray, uv: np.ndarray, inst) -> None:
    """Export only the used vertices, in ascending id order."""
    used, local = np.unique(faces, return_inverse=True)
    visual = trimesh.visual.TextureVisuals(uv=uv[used], material=inst.visual.material)
    mesh = trimesh.Trimesh(vertices=v[used], faces=local.reshape(-1, 3), visual=visual, process=False)
    scene = trimesh.Scene()
    scene.add_geometry(mesh, geom_name=inst.geometry, node_name=inst.node)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(scene.export(file_type="glb", include_normals=True))


def _write_review(path: Path, kept: trimesh.Trimesh, removed: list[trimesh.Trimesh],
                  patches: trimesh.Trimesh | None) -> None:
    """Kept surface see-through grey, so hidden removed pieces (red) show through it."""
    scene = trimesh.Scene()
    colours = {"kept": [200, 200, 200, 90], "removed": [220, 40, 40, 255], "repaired": [250, 200, 0, 255]}
    parts = (("kept", kept), ("removed", trimesh.util.concatenate(removed) if removed else None), ("repaired", patches))
    for name, mesh in parts:
        if mesh is None or len(mesh.faces) == 0:
            continue
        used, local = np.unique(mesh.faces, return_inverse=True)
        mesh = trimesh.Trimesh(mesh.vertices[used], local.reshape(-1, 3), process=False)
        mesh.visual = trimesh.visual.TextureVisuals(material=trimesh.visual.material.PBRMaterial(
            baseColorFactor=colours[name], metallicFactor=0.0, roughnessFactor=0.8, doubleSided=True,
            alphaMode="BLEND" if name == "kept" else "OPAQUE"))
        scene.add_geometry(mesh, geom_name=name, node_name=name)
    path.write_bytes(scene.export(file_type="glb"))


def extract(source: Path, target: Path, review: Path | None = None) -> dict:
    """Write the cleaned exterior of ``source`` to ``target`` and return the report.

    ``review``, if given, receives a GLB with the kept surface (see-through grey),
    removed pieces (red) and repaired patches (yellow), written even when
    cleanup refuses.
    """
    source, target = Path(source), Path(target)
    if not source.is_file():
        raise FileNotFoundError(source)
    if target.exists() or source.resolve() == target.resolve():
        raise FileExistsError(f"output already exists or equals input: {target}")

    instances, info = load_instances(source)
    if len(instances) != 1:
        raise ShellError("expected exactly one mesh instance")
    inst = instances[0]
    v, f = inst.vertices, inst.faces
    uv = getattr(inst.visual, "uv", None)
    if uv is None or len(uv) != len(v):
        raise ShellError("expected per-vertex UVs; refusing to drop texture mapping")
    diagonal = float(np.linalg.norm(v.max(axis=0) - v.min(axis=0)))
    if diagonal <= 0 or not np.isfinite(v).all():
        raise ShellError("mesh coordinates must be finite and nonzero")
    rng = np.random.default_rng(0)
    gap_limit = CONTACT_GAP * diagonal

    s = _Surface(v, np.asarray(uv, dtype=np.float64), diagonal)
    mf = s.mid[f]
    good = (mf[:, 0] != mf[:, 1]) & (mf[:, 1] != mf[:, 2]) & (mf[:, 0] != mf[:, 2])
    f, mf = f[good], mf[good]
    rows = np.concatenate([mf[:, 0], mf[:, 1], mf[:, 2]])
    cols = np.concatenate([mf[:, 1], mf[:, 2], mf[:, 0]])
    graph = coo_matrix((np.ones(len(rows), dtype=np.uint8), (rows, cols)), shape=(len(s.mv),) * 2)
    _, labels = connected_components(graph, directed=False)
    face_label = labels[mf[:, 0]]
    ids, counts = np.unique(face_label, return_counts=True)
    order = ids[np.argsort(-counts, kind="stable")]

    pieces = []
    for number, label in enumerate(order, start=1):
        sel = np.where(face_label == label)[0]
        area, volume = _area_volume(s.mv, mf[sel])
        pv = s.mv[np.unique(mf[sel])]
        pieces.append({"piece": number, "faces": len(sel), "area": area, "signed_volume": volume,
                       "bounds": [pv.min(axis=0).tolist(), pv.max(axis=0).tolist()],
                       **_edge_stats(mf[sel]), "_faces": sel})
    report: dict = {
        "source": str(source), "output": None, "status": "running", "diagonal": diagonal,
        "contact_gap": gap_limit, "degenerate_faces_dropped": int((~good).sum()),
        "pieces": pieces, "walls": {}, "repairs": {}, "texture": {}, "shape": {},
    }

    def public() -> dict:
        out = dict(report)
        out["pieces"] = [{k: val for k, val in p.items() if not k.startswith("_")} for p in pieces]
        return out

    def removed_meshes(numbers) -> list[trimesh.Trimesh]:
        return [trimesh.Trimesh(s.mv, mf[pieces[n - 1]["_faces"]], process=False) for n in numbers]

    def refuse(message: str, extra: dict | None = None):
        report.update(extra or {})
        report["status"] = "refused"
        report["reason"] = message
        if review is not None:
            kept = trimesh.Trimesh(s.v, f[pieces[0]["_faces"]], process=False)
            _write_review(Path(review), kept, removed_meshes([p["piece"] for p in pieces[1:]]), None)
            report["review"] = str(review)
        raise ShellError(message, public())

    # Outer wall: the largest piece, repaired into a closed solid.
    outer = pieces[0]
    walls = [p for p in pieces if p["area"] >= WALL_SHARE * outer["area"]]
    if len(walls) > 2:
        refuse(f"found {len(walls)} large surfaces; expected an outer wall and at most one inner wall")

    # Weld a small open piece whose every open edge vertex sits on an open edge
    # vertex of the outer wall (a crack): snap it onto those vertices.
    welded, outer_sel = [], outer["_faces"]
    outer_open = _open_vertices(mf[outer_sel])
    for piece in pieces[len(walls):]:
        if not piece["boundary_edges"] or not len(outer_open):
            continue
        piece_open = _open_vertices(mf[piece["_faces"]])
        dist, j = cKDTree(s.mv[outer_open]).query(s.mv[piece_open])
        if (dist > gap_limit).any() or len(np.unique(j)) != len(j):
            continue
        mapping = np.arange(len(s.mv))
        mapping[piece_open] = outer_open[j]
        s.mid = mapping[s.mid]
        snapped = np.isin(s.mid, outer_open[j])
        s.v[snapped] = s.mv[s.mid[snapped]]
        mf = s.mid[f]
        outer_sel = np.concatenate([outer_sel, piece["_faces"]])
        outer_open = _open_vertices(mf[outer_sel])
        piece.update(decision="fused: welded to the outer wall along matching open edges",
                     welded_vertices=int(len(piece_open)), max_weld_shift=float(dist.max()))
        welded.append(piece["piece"])
    report["repairs"]["welded_pieces"] = welded
    try:
        outer_faces, patch_mask, repair_log = _repair(s, f[outer_sel], diagonal)
        solid = _manifold(s, outer_faces)
    except ShellError as exc:
        refuse(f"outer wall (piece 1) could not be repaired: {exc}", exc.report)
    report["repairs"]["outer_wall"] = repair_log
    if solid.volume() <= 0:
        refuse("the largest surface is inside-out (negative volume); cannot identify an exterior")
    outer["decision"] = "kept as the outer wall"

    # Inner wall: a second large piece must be nested, hidden and similar.
    for wall in walls[1:]:
        wf = mf[wall["_faces"]]
        pts = _sample(s.mv, wf, rng)
        checks = {
            "inside_fraction": float(_inside(solid, pts, diagonal).mean()),
            "visible_fraction": float(_visible(solid, pts[:VISIBILITY_SAMPLES], diagonal).mean()),
            "area_ratio": wall["area"] / outer["area"],
            "volume_ratio": -wall["signed_volume"] / solid.volume(),
            "median_gap_to_outer": float(np.median(_distance(pts[:500], s.mv, mf[outer["_faces"]])[0])),
        }
        report["walls"]["inner_checks"] = checks
        nested = (checks["inside_fraction"] >= INNER_INSIDE_MIN and checks["visible_fraction"] <= INNER_VISIBLE_MAX
                  and SHELL_RATIO[0] < checks["area_ratio"] < SHELL_RATIO[1]
                  and SHELL_RATIO[0] < checks["volume_ratio"] < SHELL_RATIO[1])
        if not nested:
            refuse(f"piece {wall['piece']} is large but does not pass the inner-wall checks "
                   f"(inside {checks['inside_fraction']:.1%}, visible {checks['visible_fraction']:.1%}, "
                   f"area ratio {checks['area_ratio']:.2f}, volume ratio {checks['volume_ratio']:.2f})")
        wall["decision"] = "removed as the inner wall"
    report["walls"]["outer"] = 1
    report["walls"]["inner"] = walls[1]["piece"] if len(walls) > 1 else None

    # Smaller pieces.
    fuse = []
    outer_mf = s.mid[outer_faces]
    for piece in pieces[len(walls):]:
        if piece["piece"] in welded:
            continue
        pf = mf[piece["_faces"]]
        pts = _sample(s.mv, pf, rng)
        inside = _inside(solid, pts, diagonal)
        visible = _visible(solid, pts, diagonal, ~inside)
        dist, closest, tri_index = _distance(pts, s.mv, outer_mf)
        k = int(np.argmin(dist))
        gap = float(dist[k])
        piece["_contact"] = (pts[k], closest[k], tri_index[k])
        crossing = _crossing_edges(solid, s.mv, pf)
        piece.update(inside_fraction=float(inside.mean()), visible_fraction=float(visible.mean()),
                     gap=gap, gap_fraction_of_diagonal=gap / diagonal, crossing_edges=crossing)
        touching = crossing > 0 or gap <= gap_limit
        if inside.all() and crossing == 0:
            piece["decision"] = "removed: hidden inside the outer wall"
        elif touching and not visible.any():
            piece["decision"] = "removed: touches the outer wall but is hidden from every outside direction"
        elif touching:
            piece["decision"] = "fuse: touches or crosses the outer wall and is visible from outside"
            fuse.append(piece)
        elif not inside.any():
            piece["decision"] = "removed: separate from the outer wall"
            if visible.any():
                piece["review_flag"] = "visible feature removed; check it is not real anatomy"
        else:
            refuse(f"piece {piece['piece']} is partly inside the outer wall without touching it; "
                   "cannot classify it")

    out_v, out_f, out_uv, new_vertices = s.v, outer_faces, s.uv, np.zeros(len(s.v), dtype=bool)
    new_vertices[len(v):] = True  # hole-fill centres and seam duplicates
    if fuse:
        import manifold3d

        report["repairs"]["fused"] = []
        for piece in fuse:
            try:
                pfaces, _, plog = _repair(s, f[piece["_faces"]], diagonal)
                piece_solid = _manifold(s, pfaces)
            except ShellError as exc:
                refuse(f"piece {piece['piece']} should be fused but could not be made a closed surface: {exc}")
            if piece_solid.volume() <= 0:
                refuse(f"piece {piece['piece']} should be fused but is inside-out")
            shift = np.zeros(3)
            if piece["crossing_edges"] == 0:
                # Bridge the tiny gap: move the piece into the wall by the gap plus one tolerance.
                p, q, t_index = piece["_contact"]
                a, b, c = s.mv[outer_mf[t_index]]
                normal = np.cross(b - a, c - a)
                normal /= np.linalg.norm(normal)
                shift = -normal * (piece["gap"] + gap_limit)
                piece_solid = piece_solid.translate(tuple(shift))
            union = solid + piece_solid
            if len(union.decompose()) != 1:
                refuse(f"union with piece {piece['piece']} left separate parts; no reliable join")
            report["repairs"]["fused"].append({"piece": piece["piece"], **plog, "gap_bridged_by_shift": float(
                np.linalg.norm(shift)), "volume_added": union.volume() - solid.volume()})
            solid = union
        mesh = solid.to_mesh()
        props = np.asarray(mesh.vert_properties, dtype=np.float64)
        out_v, out_uv = props[:, :3], props[:, 3:5]
        out_f = np.asarray(mesh.tri_verts, dtype=np.int64)
        known = {tuple(r) for r in np.round(np.c_[s.v[: len(v)], s.uv[: len(v)]], 5)}
        new_vertices = np.array([tuple(r) not in known for r in np.round(props, 5)])
        patch_mask = np.zeros(len(out_f), dtype=bool)
        if not manifold3d.Manifold(mesh).status() == manifold3d.Error.NoError:
            refuse("fused surface is not a valid closed solid")

    # Shape check: removing hidden material, welding and filling holes must not
    # move the ends of the outer wall. A fused piece may extend them; that is reported.
    pv = s.mv[np.unique(mf[outer_sel])]
    centre = pv.mean(axis=0)
    axis = np.linalg.svd(pv - centre, full_matrices=False)[2][0]
    before = _axis_extent(pv, centre, axis)
    after = _axis_extent(s.v[np.unique(outer_faces)], centre, axis)
    final = _axis_extent(out_v[np.unique(out_f)], centre, axis)
    length = before[1] - before[0]
    report["shape"] = {"axis": axis.tolist(), "length_before": length, "length_after": final[1] - final[0],
                       "end_shift_from_repair": [after[0] - before[0], after[1] - before[1]],
                       "end_shift_from_fusion": [final[0] - after[0], final[1] - after[1]]}
    if max(abs(after[0] - before[0]), abs(after[1] - before[1])) > MAX_LENGTH_CHANGE * length:
        refuse("cleanup moved an end of the arm by more than "
               f"{MAX_LENGTH_CHANGE:.0%} of its length; base or tip would change")

    # Texture check at repaired vertices against the nearest original surface colour.
    used = np.unique(out_f)
    if not np.isfinite(out_uv[used]).all() or (out_uv[used] < -1e-6).any() or (out_uv[used] > 1 + 1e-6).any():
        refuse("repaired vertices have UVs outside the texture")
    check = used[new_vertices[used]]
    report["texture"] = {"new_vertices": int(len(check))}
    original_colours = _colours(inst.visual, s.uv[: len(v)])
    if len(check) and original_colours is not None:
        _, near = cKDTree(s.v[: len(v)]).query(out_v[check])
        diff = np.abs(_colours(inst.visual, out_uv[check]) - original_colours[near]).mean(axis=1)
        report["texture"].update(mean_colour_difference=float(diff.mean()), max_colour_difference=float(diff.max()))
        if diff.mean() > MAX_COLOUR_DIFF:
            refuse(f"texture at repaired vertices differs from the nearby surface (mean {diff.mean():.0f}/255)")

    _write(target, out_v, out_f, out_uv, inst)
    exported, exported_info = load_instances(target)
    build_topology(exported)
    ev, euv = exported[0].vertices, getattr(exported[0].visual, "uv", None)
    if euv is None or len(euv) != len(used) or not np.allclose(euv, out_uv[used], atol=1e-6) \
            or not np.allclose(ev, out_v[used], atol=1e-6 * diagonal):
        target.unlink(missing_ok=True)
        refuse("positions or UV coordinates changed during export")
    if exported_info["images"] < info["images"]:
        target.unlink(missing_ok=True)
        refuse("texture images were lost during export")

    report["status"] = "cleaned"
    report["output"] = str(target)
    report["output_faces"] = int(len(out_f))
    report["output_images"] = len(read_glb_json(target).get("images") or [])
    report["kept_original_outer_faces"] = int((~patch_mask).sum()) if not fuse else None
    report["removed_pieces"] = [p["piece"] for p in pieces if p.get("decision", "").startswith("removed")]
    report["fused_pieces"] = [p["piece"] for p in fuse]
    report["review_flags"] = [p["piece"] for p in pieces if p.get("review_flag")]
    if review is not None:
        kept = trimesh.Trimesh(out_v, out_f, process=False)
        patches = trimesh.Trimesh(out_v, out_f[patch_mask], process=False) if patch_mask.any() else None
        _write_review(Path(review), kept, removed_meshes(report["removed_pieces"]), patches)
        report["review"] = str(review)
    return public()


def main() -> None:
    parser = argparse.ArgumentParser(prog="epics-outer-shell", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("source", type=Path)
    parser.add_argument("target", type=Path)
    parser.add_argument("--review", type=Path, help="also write a review GLB (removed red, repaired yellow)")
    args = parser.parse_args()
    try:
        print(json.dumps(extract(args.source, args.target, args.review), indent=2, default=str))
    except ShellError as exc:
        print(json.dumps(exc.report, indent=2, default=str))
        raise SystemExit(f"error: {exc}")


if __name__ == "__main__":
    main()
