"""Mesh analysis for stub scaling: topology, plane sections, alignment.

All functions are pure NumPy/SciPy and run on CPU. Coordinates passed between
functions are float64. "Canonical" coordinates put the base plane at Z=0 with
the stub extending toward +Z.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import ConvexHull
from scipy.spatial.distance import pdist

from .errors import StubError
from .glb_io import MeshInstance

# --- alignment thresholds (fractions are of the extent along the tested axis) ---
END_REGION = 0.30  # faces this close to an end may belong to its cap
CAP_SEED_ANGLE = math.radians(25)  # seed: normals within this of the axis
CAP_ANGLE = math.radians(10)  # cap faces: normals within this of the cap plane
PLANE_TOL = 0.01  # cap/boundary planarity and "terminal plane" tolerance
REF_DEPTH = 0.08  # reference cross-section this far inside the end
BASE_MIN_SCORE = 0.5  # planar area / nearby cross-section area to count as a base
TIP_MAX_SCORE = 0.2  # at most this to count as a non-planar tip
AXIS_CLEAR_RATIO = 1.3  # PCA: largest / second variance to call the long axis clear
MAX_BASE_AXIS_ANGLE = 25.0  # degrees between base normal and a clear long axis
# --- straightness (checked on canonical sections) ---
PROFILE_FRACTIONS = tuple(np.round(np.arange(0.10, 0.901, 0.05), 2))
MAX_LEAN_DEG = 15.0  # centreline tilt relative to the base normal
MAX_BEND = 0.05  # centreline deviation from a straight line, fraction of length
# --- sections ---
MAX_CONTOUR_EXCESS = 0.01  # contour vs taut-tape (hull) perimeter; above this they differ
RESAMPLE_POINTS = 100  # contour resampling for the excess, suppresses facet noise
MINOR_LOOP = 0.01  # loops below this fraction of the main loop area are ignored
STRAY_SURFACE = 0.002  # separate surfaces above this share of area are rejected


@dataclass
class Topology:
    """Instances merged by position so UV/material seams do not split surfaces."""

    vertices: np.ndarray  # (n, 3) unique positions
    faces: np.ndarray  # (m, 3) non-degenerate faces into ``vertices``
    instance_index: list[np.ndarray]  # per instance: its vertex -> merged vertex
    warnings: list[str] = field(default_factory=list)


def build_topology(instances: list[MeshInstance]) -> Topology:
    all_v = np.concatenate([i.vertices for i in instances])
    if not np.isfinite(all_v).all():
        raise StubError("unsupported_mesh", "mesh has non-finite vertex coordinates")
    diag = float(np.linalg.norm(all_v.max(0) - all_v.min(0)))
    if diag <= 0:
        raise StubError("unsupported_mesh", "mesh has zero size")
    quantum = diag * 1e-7
    keys = np.round(all_v / quantum).astype(np.int64)
    _, first, inverse = np.unique(keys, axis=0, return_index=True, return_inverse=True)
    inverse = inverse.ravel()
    vertices = all_v[first]

    faces, index, offset = [], [], 0
    for inst in instances:
        idx = inverse[offset : offset + len(inst.vertices)]
        index.append(idx)
        faces.append(idx[inst.faces])
        offset += len(inst.vertices)
    faces = np.concatenate(faces)
    good = (faces[:, 0] != faces[:, 1]) & (faces[:, 1] != faces[:, 2]) & (faces[:, 0] != faces[:, 2])
    faces = faces[good]
    topo = Topology(vertices, faces, index)
    _check_components(topo)
    return topo


def face_geometry(v: np.ndarray, f: np.ndarray):
    tri = v[f]
    cross = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])
    double = np.linalg.norm(cross, axis=1)
    area = 0.5 * double
    normal = cross / np.where(double > 0, double, 1.0)[:, None]
    return area, normal, tri.mean(axis=1)


def _check_components(topo: Topology) -> None:
    n = len(topo.vertices)
    f = topo.faces
    rows = np.concatenate([f[:, 0], f[:, 1], f[:, 2]])
    cols = np.concatenate([f[:, 1], f[:, 2], f[:, 0]])
    graph = coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))
    count, labels = connected_components(graph, directed=False)
    if count == 1:
        return
    area, _, _ = face_geometry(topo.vertices, f)
    comp_area = np.bincount(labels[f[:, 0]], weights=area, minlength=count)
    total = comp_area.sum()
    order = np.argsort(comp_area)[::-1]
    stray = [a / total for a in comp_area[order[1:]] if a > 0]
    large = [s for s in stray if s > STRAY_SURFACE]
    if large:
        raise StubError(
            "unsupported_mesh",
            f"mesh has {len(large) + 1} separate surfaces (largest extra holds "
            f"{100 * max(large):.1f}% of the area). Remove background/extra geometry "
            "upstream so the GLB contains only the stub.",
            {"surface_area_shares": [round(1 - sum(stray), 5)] + [round(s, 5) for s in stray]},
        )
    if stray:
        topo.warnings.append(
            f"{len(stray)} tiny detached fragment(s) ({100 * sum(stray):.3f}% of area) "
            "were kept and deformed with the stub"
        )


def boundary_loops(f: np.ndarray) -> list[np.ndarray]:
    """Closed chains of edges used by exactly one face."""
    edges = np.sort(np.concatenate([f[:, [0, 1]], f[:, [1, 2]], f[:, [2, 0]]]), axis=1)
    uniq, counts = np.unique(edges, axis=0, return_counts=True)
    boundary = uniq[counts == 1]
    if len(boundary) == 0:
        return []
    return [loop for loop, closed in _chains(boundary) if closed]


def _chains(segments: np.ndarray) -> list[tuple[np.ndarray, bool]]:
    """Split an edge list into connected chains; flag those that are simple loops."""
    ids, local = np.unique(segments, return_inverse=True)
    local = local.reshape(-1, 2)
    n = len(ids)
    deg = np.bincount(local.ravel(), minlength=n)
    graph = coo_matrix((np.ones(len(local)), (local[:, 0], local[:, 1])), shape=(n, n))
    count, labels = connected_components(graph, directed=False)
    nbrs: list[list[int]] = [[] for _ in range(n)]
    for a, b in local:
        nbrs[a].append(b)
        nbrs[b].append(a)
    out = []
    for c in range(count):
        members = np.flatnonzero(labels == c)
        if not (deg[members] == 2).all() or len(members) < 3:
            out.append((ids[members], False))
            continue
        start = members[0]
        order, prev, cur = [start], -1, start
        while True:
            a, b = nbrs[cur]
            nxt = a if a != prev else b
            if nxt == start:
                break
            order.append(nxt)
            prev, cur = cur, nxt
        out.append((ids[np.array(order)], True))
    return out


@dataclass
class Slice:
    loops: list[np.ndarray]  # closed loops, (k, 2) planar coordinates
    open_chains: int  # chains that do not close (holes or open surfaces)


def slice_mesh(planar: np.ndarray, height: np.ndarray, f: np.ndarray, scale: float) -> Slice:
    """Intersect a mesh with the plane height == 0.

    ``planar`` holds each vertex's in-plane 2D coordinates and ``height`` its
    signed distance to the plane. Vertices lying exactly on the plane are
    avoided by a deterministic sub-micro shift of the plane.
    """
    eps = 1e-10 * scale
    for shift in (0.0, 1e-8, -1e-8, 3e-8, -3e-8):
        h = height - shift * scale
        if not np.any(np.abs(h) < eps):
            break
    pos = h > 0
    n_pos = pos[f].sum(axis=1)
    cf = f[(n_pos == 1) | (n_pos == 2)]
    if len(cf) == 0:
        return Slice([], 0)
    e = np.stack([cf[:, [0, 1]], cf[:, [1, 2]], cf[:, [2, 0]]], axis=1)
    crosses = pos[e[..., 0]] != pos[e[..., 1]]
    ce = np.sort(e[crosses].reshape(-1, 2), axis=1)  # two crossing edges per face
    key = ce[:, 0] * (len(height) + 1) + ce[:, 1]
    uniq, first, inv = np.unique(key, return_index=True, return_inverse=True)
    a, b = ce[first, 0], ce[first, 1]
    t = h[a] / (h[a] - h[b])
    pts = planar[a] + t[:, None] * (planar[b] - planar[a])
    segments = inv.reshape(-1, 2)
    loops, open_chains = [], 0
    for chain, closed in _chains(segments):
        if closed:
            loops.append(pts[chain])
        else:
            open_chains += 1
    return Slice(loops, open_chains)


def polygon_area_centroid(xy: np.ndarray) -> tuple[float, np.ndarray]:
    x, y = xy[:, 0], xy[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    cross = x * yn - xn * y
    a = cross.sum() / 2
    if abs(a) < 1e-300:
        return 0.0, xy.mean(axis=0)
    cx = ((x + xn) * cross).sum() / (6 * a)
    cy = ((y + yn) * cross).sum() / (6 * a)
    return abs(a), np.array([cx, cy])


def closed_perimeter(xy: np.ndarray) -> float:
    return float(np.linalg.norm(np.roll(xy, -1, axis=0) - xy, axis=1).sum())


@dataclass
class Section:
    z: float
    circumference: float  # convex-hull perimeter: what a taut tape follows
    max_width: float  # largest straight-across span (caliper)
    contour_perimeter: float
    contour_excess: float  # resampled contour perimeter / hull perimeter - 1
    area: float
    solidity: float
    centroid: np.ndarray
    hull: np.ndarray  # convex-hull vertices in order


def _resample(loop: np.ndarray, n: int) -> np.ndarray:
    closed = np.vstack([loop, loop[:1]])
    s = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(closed, axis=0), axis=1))])
    t = np.linspace(0, s[-1], n, endpoint=False)
    return np.stack([np.interp(t, s, closed[:, 0]), np.interp(t, s, closed[:, 1])], axis=1)


def section_from_loop(loop: np.ndarray, z: float) -> Section:
    area, centroid = polygon_area_centroid(loop)
    hull = ConvexHull(loop)
    hv = loop[hull.vertices]
    circumference = closed_perimeter(hv)
    return Section(
        z=z,
        circumference=circumference,
        max_width=float(pdist(hv).max()),
        contour_perimeter=closed_perimeter(loop),
        contour_excess=closed_perimeter(_resample(loop, RESAMPLE_POINTS)) / circumference - 1,
        area=area,
        solidity=area / hull.volume if hull.volume > 0 else 0.0,
        centroid=centroid,
        hull=hv,
    )


def main_loop(sl: Slice, where: str) -> tuple[np.ndarray, int]:
    """Return the one significant closed loop of a slice, or raise."""
    if sl.open_chains:
        raise StubError(
            "section_unsupported",
            f"cross-section {where} is not a closed loop ({sl.open_chains} open "
            "chain(s)); the surface has a hole or opening there.",
        )
    if not sl.loops:
        raise StubError("section_unsupported", f"no cross-section found {where}")
    areas = np.array([polygon_area_centroid(l)[0] for l in sl.loops])
    biggest = int(np.argmax(areas))
    significant = np.flatnonzero(areas >= MINOR_LOOP * areas[biggest])
    others = [i for i in significant if i != biggest]
    if others:
        outer = sl.loops[biggest]
        nested = [i for i in others if point_in_polygon(sl.loops[i][0], outer)]
        if len(nested) == len(others):
            raise StubError(
                "section_unsupported",
                f"cross-section {where} has an inner wall inside the outer surface "
                f"(inner loop is {100 * areas[nested].max() / areas[biggest]:.0f}% of the "
                "outer area): the mesh is a hollow, double-walled shell rather than a "
                "solid stub surface.",
                {"loop_area_ratios": (areas[significant] / areas[biggest]).round(4).tolist()},
            )
        raise StubError(
            "section_unsupported",
            f"cross-section {where} has {len(others) - len(nested) + 1} side-by-side loops; "
            "branches such as fingers are not supported by this scaler.",
            {"loop_area_ratios": (areas[significant] / areas[biggest]).round(4).tolist()},
        )
    return sl.loops[biggest], len(sl.loops) - 1


def point_in_polygon(p: np.ndarray, poly: np.ndarray) -> bool:
    """Even-odd ray casting."""
    x, y = poly[:, 0], poly[:, 1]
    xn, yn = np.roll(x, -1), np.roll(y, -1)
    straddle = (y > p[1]) != (yn > p[1])
    with np.errstate(divide="ignore", invalid="ignore"):
        x_cross = x + (p[1] - y) * (xn - x) / (yn - y)
    return bool(np.count_nonzero(straddle & (p[0] < x_cross)) % 2)


def canonical_section(vc: np.ndarray, f: np.ndarray, z: float, where: str) -> Section:
    scale = float(vc[:, 2].max() - vc[:, 2].min())
    loop, _ = main_loop(slice_mesh(vc[:, :2], vc[:, 2] - z, f, scale), where)
    return section_from_loop(loop, z)


# ------------------------------------------------------------------ alignment


@dataclass
class EndCandidate:
    direction: np.ndarray  # outward test direction
    kind: str  # "closed_cap" | "open_boundary" | "none"
    score: float
    normal: np.ndarray  # outward normal of the planar region
    point: np.ndarray  # a point on the planar region (area centroid)

    def summary(self) -> dict:
        return {"kind": self.kind, "planar_area_ratio": round(self.score, 4)}


@dataclass
class Alignment:
    rotation: np.ndarray  # rows are canonical X, Y, Z in source coordinates
    origin: np.ndarray  # base-plane point in source coordinates
    length: float  # source units, base to furthest tip point
    base: EndCandidate
    tip: EndCandidate
    pca_ratio: float
    base_axis_angle_deg: float
    notes: list[str]

    def canonical(self, v: np.ndarray) -> np.ndarray:
        return (v - self.origin) @ self.rotation.T

    def summary(self) -> dict:
        return {
            "base_type": self.base.kind,
            "base_planar_area_ratio": round(self.base.score, 4),
            "tip_planar_area_ratio": round(self.tip.score, 4),
            "pca_variance_ratio": round(self.pca_ratio, 4),
            "base_normal_vs_long_axis_deg": round(self.base_axis_angle_deg, 3),
            "notes": self.notes,
        }


def _plane_basis(n: np.ndarray) -> np.ndarray:
    helper = np.array([1.0, 0, 0]) if abs(n[0]) < 0.9 else np.array([0, 1.0, 0])
    u = np.cross(n, helper)
    u /= np.linalg.norm(u)
    return np.stack([u, np.cross(n, u)])


def _reference_area(v: np.ndarray, f: np.ndarray, n: np.ndarray, top: float, extent: float) -> float:
    basis = _plane_basis(n)
    sl = slice_mesh(v @ basis.T, v @ n - (top - REF_DEPTH * extent), f, extent)
    if not sl.loops:
        return 0.0
    return max(polygon_area_centroid(l)[0] for l in sl.loops)


def _evaluate_end(v, f, area, normal, cent, loops, direction) -> EndCandidate:
    t = v @ direction
    t_max, extent = t.max(), t.max() - t.min()
    tol = PLANE_TOL * extent
    near = cent @ direction >= t_max - END_REGION * extent
    best = EndCandidate(direction, "none", 0.0, direction, v[np.argmax(t)])

    nd = normal @ direction
    seed = near & (np.abs(nd) > math.cos(CAP_SEED_ANGLE))
    if seed.any():
        nbar = direction.copy()
        sel = seed
        for _ in range(4):
            s = np.sign(normal[sel] @ nbar)
            m = (normal[sel] * (s * area[sel])[:, None]).sum(axis=0)
            if np.linalg.norm(m) == 0:
                break
            nbar = m / np.linalg.norm(m)
            top = (v @ nbar).max()
            sel = near & (np.abs(normal @ nbar) > math.cos(CAP_ANGLE)) & (cent @ nbar >= top - tol)
            if not sel.any():
                break
        if sel.any():
            cap_area = area[sel].sum()
            top = (v @ nbar).max()
            ref = _reference_area(v, f, nbar, top, (v @ nbar).max() - (v @ nbar).min())
            if ref > 0:
                point = (cent[sel] * area[sel, None]).sum(axis=0) / cap_area
                best = EndCandidate(direction, "closed_cap", float(cap_area / ref), nbar, point)

    for loop in loops:
        pts = v[loop]
        c = pts.mean(axis=0)
        if c @ direction < t_max - END_REGION * extent:
            continue
        nl = np.linalg.svd(pts - c)[2][2]
        if nl @ direction < 0:
            nl = -nl
        if nl @ direction < math.cos(CAP_SEED_ANGLE):
            continue
        if np.sqrt(np.mean(((pts - c) @ nl) ** 2)) > tol or (v @ nl).max() - c @ nl > tol:
            continue
        loop_area, _ = polygon_area_centroid((pts - c) @ _plane_basis(nl).T)
        top = (v @ nl).max()
        ref = _reference_area(v, f, nl, top, top - (v @ nl).min())
        if ref > 0 and loop_area / ref > best.score:
            best = EndCandidate(direction, "open_boundary", float(loop_area / ref), nl, c)
    return best


def detect_alignment(topo: Topology) -> Alignment:
    """Find the base cutoff and long axis; raise ``alignment_ambiguous`` if unclear."""
    v, f = topo.vertices, topo.faces
    area, normal, cent = face_geometry(v, f)
    w = area / area.sum()
    mu = w @ cent
    d = cent - mu
    cov = (d * w[:, None]).T @ d
    evals, evecs = np.linalg.eigh(cov)
    pca_axis = evecs[:, 2]
    pca_ratio = float(evals[2] / max(evals[1], 1e-300))
    loops = boundary_loops(f)

    ends = {}
    for k in (2, 1, 0):
        for sign in (1, -1):
            ends[(k, sign)] = _evaluate_end(v, f, area, normal, cent, loops, sign * evecs[:, k])
    scores = {f"axis{k}{'+' if s > 0 else '-'}": round(e.score, 4) for (k, s), e in ends.items()}
    bases = [key for key, e in ends.items() if e.score >= BASE_MIN_SCORE]
    details = {"end_planar_area_ratios": scores, "pca_variance_ratio": round(pca_ratio, 4)}

    if not bases:
        raise StubError(
            "alignment_ambiguous",
            "no substantial planar base cutoff found at either end. The scaler "
            "needs one flat cut (or flat opening) marking the base; recapture or "
            "crop so the base boundary is clear and regenerate.",
            details,
        )
    if len(bases) > 1:
        raise StubError(
            "alignment_ambiguous",
            f"{len(bases)} ends look like planar bases; cannot tell base from tip.",
            details,
        )
    (k, sign) = bases[0]
    base, tip = ends[(k, sign)], ends[(k, -sign)]
    if tip.score > TIP_MAX_SCORE:
        raise StubError(
            "alignment_ambiguous",
            f"the end opposite the base is partly flat (planar area ratio "
            f"{tip.score:.2f}); both ends may be cut, so base and tip are ambiguous.",
            details,
        )

    z_axis = -base.normal
    angle = math.degrees(math.acos(min(1.0, abs(float(z_axis @ pca_axis)))))
    notes = []
    if pca_ratio >= AXIS_CLEAR_RATIO:
        if angle > MAX_BASE_AXIS_ANGLE:
            raise StubError(
                "alignment_ambiguous",
                f"the base plane is {angle:.0f} degrees off perpendicular to the "
                "long axis; the axis is unclear or the cut is too oblique.",
                details,
            )
    else:
        notes.append("long axis from surface spread was weak; axis taken from the base normal")

    proj = np.eye(3) - np.outer(z_axis, z_axis)
    tcov = proj @ cov @ proj
    x_axis = np.linalg.eigh(tcov)[1][:, 2]
    x_axis -= (x_axis @ z_axis) * z_axis
    x_axis /= np.linalg.norm(x_axis)
    if x_axis[np.argmax(np.abs(x_axis))] < 0:
        x_axis = -x_axis
    rotation = np.stack([x_axis, np.cross(z_axis, x_axis), z_axis])
    al = Alignment(rotation, base.point.copy(), 0.0, base, tip, pca_ratio, angle, notes)
    z = al.canonical(v)[:, 2]
    al.length = float(z.max())
    if z.min() < -0.02 * al.length:
        raise StubError(
            "alignment_ambiguous",
            "geometry extends below the detected base plane; the base is not terminal.",
            details,
        )
    return al


@dataclass
class Profile:
    sections: list[Section]
    lean_deg: float
    bend: float  # max centreline deviation from a line, fraction of length


def profile(vc: np.ndarray, f: np.ndarray, length: float) -> Profile:
    """Sections along the stub, used for straightness and the deformation centreline."""
    secs = [
        canonical_section(vc, f, fr * length, f"at {fr:.0%} of the length")
        for fr in PROFILE_FRACTIONS
    ]
    z = np.array([s.z for s in secs])
    c = np.array([s.centroid for s in secs])
    A = np.stack([np.ones_like(z), z], axis=1)
    coef, *_ = np.linalg.lstsq(A, c, rcond=None)
    lean = math.degrees(math.atan(float(np.linalg.norm(coef[1]))))
    bend = float(np.linalg.norm(c - A @ coef, axis=1).max() / length)
    return Profile(secs, lean, bend)


def check_straight(p: Profile) -> None:
    details = {"lean_deg": round(p.lean_deg, 2), "bend_fraction_of_length": round(p.bend, 4)}
    if p.lean_deg > MAX_LEAN_DEG:
        raise StubError(
            "alignment_ambiguous",
            f"the stub leans {p.lean_deg:.1f} degrees from the base normal (limit "
            f"{MAX_LEAN_DEG:.0f}); its length direction is unclear.",
            details,
        )
    if p.bend > MAX_BEND:
        raise StubError(
            "alignment_ambiguous",
            f"the stub centreline bends by {100 * p.bend:.1f}% of its length (limit "
            f"{100 * MAX_BEND:.0f}%); only roughly straight stubs are supported.",
            details,
        )


def check_convex(sec: Section, where: str) -> None:
    if sec.contour_excess > MAX_CONTOUR_EXCESS:
        raise StubError(
            "section_unsupported",
            f"cross-section {where} is strongly concave: its contour is "
            f"{100 * sec.contour_excess:.1f}% longer than a taut tape around it "
            f"(limit {100 * MAX_CONTOUR_EXCESS:.0f}%), so a tape circumference cannot "
            "be matched to it reliably.",
            {"contour_excess": round(sec.contour_excess, 4), "solidity": round(sec.solidity, 4)},
        )
