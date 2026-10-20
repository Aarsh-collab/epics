"""Independent dimensional QA for limb-like GLB meshes.

Checks a mesh against submitted measurements; it NEVER rescales a mesh to fit
them. Assumptions that affect results (units, axis, proximal direction) are
configurable and recorded in the JSON report. Intended for engineering QA, not
clinical decisions.

Unlike ``epics-measure-stub``, which reuses the scaler's own alignment and
taut-tape definition, this measures the exact section perimeter along an axis
the operator declares, so it is a second, differently-built check.

Accepted measurement inputs:

- ``stub_measurements`` version 1 (``schemas/stub_measurements.v1.schema.json``)
- a ``capture.v1`` record from the web app (``web/``)
- the original validator shape: ``{"length_mm": ..., "stations": [{"name": "25%", ...}]}``
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import trimesh

DEFAULT_TOLERANCES = {
    "pass_mm": 2.0,
    "warn_mm": 5.0,
}
STATION_PCTS = (25, 50, 75)


def load_json(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | Path, obj: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, allow_nan=False)
        f.write("\n")


def normalize_submitted(data: Mapping[str, Any]) -> dict[str, Any]:
    """Convert any accepted measurement document to ``{length_mm, stations}``."""
    if data.get("schema_version") == "capture.v1":
        stations = []
        for s in data.get("sections", []):
            value = s.get("value")
            stations.append({
                "name": f"{round(float(s['fraction']) * 100)}%",
                "circumference_mm": value,
                "unmeasurable": value is None or s.get("reliable") is False,
            })
        length = data.get("length", {}).get("value")
        return {"length_mm": length, "stations": stations}
    if "sections" in data:  # stub_measurements v1
        stations = [
            {"name": f"{round(float(s['fraction']) * 100)}%", "circumference_mm": s.get("circumference_mm")}
            for s in data["sections"]
        ]
        return {"length_mm": data.get("length_mm"), "stations": stations}
    return {"length_mm": data.get("length_mm"), "stations": list(data.get("stations", []))}


def _scene_to_mesh(path: str | Path) -> trimesh.Trimesh:
    """Load a GLB as a scene and bake scene-node transforms into geometry."""
    loaded = trimesh.load(str(path), force="scene", process=False)
    if not isinstance(loaded, trimesh.Scene):
        raise ValueError(f"Unsupported file contents: {type(loaded).__name__}")
    # to_geometry() applies graph transforms to each instance.
    combined = loaded.to_geometry()
    if not isinstance(combined, trimesh.Trimesh) or not len(combined.faces):
        raise ValueError("No triangle mesh geometry found in GLB.")
    if not np.isfinite(combined.vertices).all():
        raise ValueError("Mesh contains non-finite vertex coordinates.")
    return combined


def _axis_vector(axis: str) -> np.ndarray:
    axis = axis.lower()
    if axis not in {"x", "y", "z", "-x", "-y", "-z"}:
        raise ValueError("axis must be one of x, y, z, -x, -y, -z")
    v = np.zeros(3, dtype=float)
    v["xyz".index(axis[-1])] = -1.0 if axis.startswith("-") else 1.0
    return v


def _pca_axis(vertices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    centered = vertices - vertices.mean(axis=0)
    vals, vecs = np.linalg.eigh(np.cov(centered, rowvar=False))
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    axis = vecs[:, 0] / np.linalg.norm(vecs[:, 0])
    # PCA sign is arbitrary; make it deterministic so --proximal-end is meaningful.
    if axis[int(np.argmax(np.abs(axis)))] < 0:
        axis = -axis
    return axis, vals


def _perimeter_of_path(path: trimesh.path.Path3D) -> tuple[float | None, int, bool]:
    """Return total perimeter only for exactly one closed path loop."""
    if not len(path.entities):
        return None, 0, False
    loops = []
    open_found = False
    for points in path.discrete:
        points = np.asarray(points)
        if len(points) < 3 or np.linalg.norm(points[0] - points[-1]) > 1e-5:
            open_found = True
            continue
        loops.append(float(np.linalg.norm(np.diff(points, axis=0), axis=1).sum()))
    if len(loops) != 1 or open_found:
        return None, len(loops), open_found
    return loops[0], 1, False


def _section_perimeter(mesh, long_axis, center, projection) -> tuple[float | None, int, bool]:
    origin = center + long_axis * (projection - float(center @ long_axis))
    section = mesh.section(plane_origin=origin, plane_normal=long_axis)
    return (None, 0, False) if section is None else _perimeter_of_path(section)


def _mesh_defects(mesh: trimesh.Trimesh) -> dict[str, Any]:
    try:
        components = mesh.split(only_watertight=False)
        component_count = len(components)
        component_faces = sorted((int(len(c.faces)) for c in components), reverse=True)
    except Exception:
        component_count, component_faces = None, []
    try:
        # Faces per unique edge: 1 = boundary, >2 = non-manifold.
        counts = np.bincount(mesh.edges_unique_inverse, minlength=len(mesh.edges_unique))
        nonmanifold_edges = int(np.sum(counts > 2))
        boundary_edges = int(np.sum(counts == 1))
    except Exception:
        nonmanifold_edges, boundary_edges = None, None
    try:
        duplicate_faces = int(len(mesh.faces) - len(np.unique(np.sort(mesh.faces, axis=1), axis=0)))
    except Exception:
        duplicate_faces = None
    try:
        degenerate_faces = int(np.sum(mesh.area_faces <= 1e-12))
    except Exception:
        degenerate_faces = None
    return {
        "is_watertight": bool(mesh.is_watertight),
        "is_winding_consistent": bool(mesh.is_winding_consistent),
        "vertex_count": int(len(mesh.vertices)),
        "face_count": int(len(mesh.faces)),
        "component_count": component_count,
        "component_face_counts_desc": component_faces,
        "boundary_edge_count": boundary_edges,
        "nonmanifold_edge_count": nonmanifold_edges,
        "duplicate_face_count": duplicate_faces,
        "degenerate_face_count": degenerate_faces,
        "euler_number": int(mesh.euler_number),
    }


def _status(abs_diff: float, tolerances: dict[str, Any]) -> str:
    if abs_diff <= float(tolerances["pass_mm"]):
        return "ok"
    if abs_diff <= float(tolerances["warn_mm"]):
        return "flagged"
    return "fail"


def evaluate(
    glb_path: str | Path,
    submitted: Mapping[str, Any],
    *,
    mesh_units: str = "m",
    axis_mode: str = "pca",
    axis: str = "z",
    proximal_end: str = "min",
    tolerances: dict[str, Any] | None = None,
    spread_offset_pct: float = 2.0,
) -> dict[str, Any]:
    """Compare a GLB against submitted measurements and return a report dict.

    ``axis_mode="canonical"`` is for ``epics-scale-stub`` output, which is
    exported in metres with the base at Z=0 and the tip toward +Z; it forces
    axis z, proximal end min and metre units.
    """
    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    if axis_mode == "canonical":
        mesh_units, axis, proximal_end = "m", "z", "min"
    if mesh_units not in {"m", "mm", "cm"}:
        raise ValueError("mesh_units must be m, cm, or mm")
    if proximal_end not in {"min", "max"}:
        raise ValueError("proximal_end must be min or max")
    if axis_mode not in {"pca", "fixed", "canonical"}:
        raise ValueError("axis_mode must be pca, fixed, or canonical")
    submitted = normalize_submitted(submitted)

    mesh = _scene_to_mesh(glb_path)
    unit_to_mm = {"m": 1000.0, "cm": 10.0, "mm": 1.0}[mesh_units]
    mesh.apply_scale(unit_to_mm)
    vertices = np.asarray(mesh.vertices)

    pca_axis, eigenvalues = _pca_axis(vertices)
    if axis_mode == "pca":
        long_axis = pca_axis
        axis_source = "principal component 1 (PCA)"
    else:
        long_axis = _axis_vector(axis)
        axis_source = f"{axis_mode} axis {axis}"

    projected = vertices @ long_axis
    pmin, pmax = float(projected.min()), float(projected.max())
    length_mm = pmax - pmin
    if length_mm <= 0:
        raise ValueError("Mesh has zero or invalid extent along selected axis.")
    center = vertices.mean(axis=0)

    def projection_at(pct: float) -> float:
        d = length_mm * pct / 100.0
        return pmin + d if proximal_end == "min" else pmax - d

    submitted_length = submitted.get("length_mm")
    length_diff = length_pct = None
    if isinstance(submitted_length, (int, float)) and submitted_length != 0:
        length_diff = length_mm - float(submitted_length)
        length_pct = 100.0 * length_diff / float(submitted_length)
    length_status = "not_verified" if length_diff is None else _status(abs(length_diff), tol)

    stations_in = submitted.get("stations", [])
    station_results = []
    not_verified = []
    for target_pct in STATION_PCTS:
        name = f"{target_pct}%"
        record = next((s for s in stations_in if str(s.get("name", "")).replace(" ", "") == name), None)
        if record is None:
            station_results.append({"name": name, "submitted_mm": None, "measured_mm": None,
                                    "diff_mm": None, "diff_pct": None, "status": "not_verified",
                                    "reason": "No submitted station record."})
            not_verified.append(f"{name} circumference: no submitted record")
            continue
        if record.get("unmeasurable") is True or record.get("circumference_mm") is None:
            station_results.append({"name": name, "submitted_mm": None, "measured_mm": None,
                                    "diff_mm": None, "diff_pct": None, "status": "not_verified",
                                    "reason": "Submitted as unmeasurable/null; not compared."})
            not_verified.append(f"{name} circumference: submitted as unmeasurable")
            continue

        submitted_c = float(record["circumference_mm"])
        measured_c, loop_count, open_loop = _section_perimeter(mesh, long_axis, center, projection_at(target_pct))
        spread_values = []
        spread_notes = []
        for offset in (-spread_offset_pct, spread_offset_pct):
            pct = target_pct + offset
            if not 0 < pct < 100:
                continue
            perim, loops, opened = _section_perimeter(mesh, long_axis, center, projection_at(pct))
            if perim is not None:
                spread_values.append(perim)
            else:
                spread_notes.append({"offset_pct": offset, "loop_count": loops, "open_loop": opened})
        if measured_c is None:
            station_results.append({
                "name": name, "submitted_mm": submitted_c, "measured_mm": None,
                "diff_mm": None, "diff_pct": None, "status": "flagged",
                "reason": "Cross-section was absent, open, or had multiple loops; no perimeter guessed.",
                "loop_count": loop_count, "open_loop": open_loop,
                "nearby_slice_spread_mm": None, "nearby_slice_failures": spread_notes,
            })
            not_verified.append(f"{name} circumference: section could not be measured unambiguously")
            continue
        diff = measured_c - submitted_c
        station_results.append({
            "name": name, "submitted_mm": submitted_c, "measured_mm": measured_c,
            "diff_mm": diff, "diff_pct": 100.0 * diff / submitted_c, "status": _status(abs(diff), tol),
            "loop_count": loop_count, "open_loop": open_loop,
            "nearby_slice_spread_mm": (max(spread_values) - min(spread_values)) if len(spread_values) >= 2 else None,
            "nearby_slice_measurements_mm": spread_values,
            "nearby_slice_failures": spread_notes,
        })

    defects = _mesh_defects(mesh)
    checks = {
        "units": {"input_mesh_units": mesh_units, "converted_to_mm": True, "scale_factor": unit_to_mm,
                  "status": "assumed; verify against exporter documentation"},
        "orientation": {"axis_mode": axis_mode, "axis_source": axis_source,
                        "axis_vector_xyz": [float(x) for x in long_axis],
                        "proximal_end": proximal_end,
                        "status": "assumed/configured; verify against scaling-team convention"},
        "pca_eigenvalues": [float(x) for x in eigenvalues],
        "mesh_defects": defects,
        "scale_sanity": {"length_mm": length_mm,
                         "warning": "Check for 10x/1000x unit mistakes against known expected size."
                                    if length_mm < 1 or length_mm > 3000 else None},
        "tolerances": {"values": tol, "label": "team-defined engineering QA thresholds; not clinical thresholds"},
    }
    comparisons = [length_status] + [s["status"] for s in station_results if s["status"] != "not_verified"]
    if "fail" in comparisons:
        overall = "fail"
    elif ("flagged" in comparisons or not_verified or not defects["is_watertight"]
          or (defects["nonmanifold_edge_count"] or 0) > 0):
        overall = "warn"
    else:
        overall = "pass"
    if length_diff is None:
        not_verified.append("Overall length: no submitted length_mm")
    not_verified.extend([
        "Accuracy of the user's tape measurements is not independently verified.",
        "Correctness of proximal/distal endpoint selection on the source photo is not verified.",
        "Fit, comfort, and clinical suitability are not evaluated.",
        "Mesh fidelity to the real limb is not evaluated; this checks only agreement with submitted values.",
        "Side-photo visual comparison is not performed.",
    ])
    return {
        "overall_status": overall,
        "length": {"submitted_mm": submitted_length, "measured_mm": length_mm,
                   "diff_mm": length_diff, "diff_pct": length_pct, "status": length_status},
        "stations": station_results,
        "checks": checks,
        "not_verified": not_verified,
        "summary": _summary(overall, length_diff, station_results, defects),
    }


def _summary(overall: str, length_diff: float | None, stations: list[dict[str, Any]], defects: dict[str, Any]) -> str:
    parts = [f"Dimensional QA status: {overall.upper()}."]
    if length_diff is not None:
        parts.append(f"Mesh length differs from submitted length by {length_diff:+.2f} mm.")
    measured = [s for s in stations if s.get("diff_mm") is not None]
    if measured:
        flagged = [s["name"] for s in measured if s["status"] != "ok"]
        parts.append("Circumference stations outside pass tolerance: " + (", ".join(flagged) if flagged else "none") + ".")
    if not defects["is_watertight"]:
        parts.append("Mesh is not watertight.")
    parts.append("This is engineering QA only, not a clinical-suitability assessment.")
    return " ".join(parts)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="epics-validate", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("glb", help="Input GLB file")
    p.add_argument("submitted_json", help="Measurement JSON (stub_measurements v1, capture.v1, or stations)")
    p.add_argument("-o", "--output", default="measurement_report.json")
    p.add_argument("--mesh-units", choices=["m", "cm", "mm"], default="m",
                   help="Units of vertex coordinates before conversion (default: m, glTF convention)")
    p.add_argument("--axis-mode", choices=["pca", "fixed", "canonical"], default="pca",
                   help="canonical = epics-scale-stub output (base at Z=0, tip +Z, metres)")
    p.add_argument("--axis", choices=["x", "y", "z", "-x", "-y", "-z"], default="z",
                   help="Used only with --axis-mode fixed")
    p.add_argument("--proximal-end", choices=["min", "max"], default="min",
                   help="Which end along the selected axis is proximal")
    p.add_argument("--pass-mm", type=float, default=DEFAULT_TOLERANCES["pass_mm"])
    p.add_argument("--warn-mm", type=float, default=DEFAULT_TOLERANCES["warn_mm"])
    p.add_argument("--spread-offset-pct", type=float, default=2.0)
    args = p.parse_args(argv)
    if not math.isfinite(args.pass_mm) or not math.isfinite(args.warn_mm) or args.pass_mm > args.warn_mm:
        p.error("--pass-mm must be finite and not greater than --warn-mm")
    report = evaluate(args.glb, load_json(args.submitted_json), mesh_units=args.mesh_units,
                      axis_mode=args.axis_mode, axis=args.axis,
                      proximal_end=args.proximal_end,
                      tolerances={"pass_mm": args.pass_mm, "warn_mm": args.warn_mm},
                      spread_offset_pct=args.spread_offset_pct)
    write_json(args.output, report)
    print(report["summary"])
    print(f"Report written to: {args.output}")
    return {"pass": 0, "warn": 0, "fail": 1}[report["overall_status"]]


if __name__ == "__main__":
    raise SystemExit(main())
