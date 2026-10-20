#!/usr/bin/env python3
"""Independent dimensional QA for limb-like GLB meshes.

This checks a mesh against submitted measurements; it NEVER rescales a mesh to fit them.
Assumptions that affect results (units, axis, proximal direction) are configurable and
recorded in the JSON report. Intended for engineering QA, not clinical decisions.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import trimesh


DEFAULT_TOLERANCES = {
    "pass_mm": 2.0,
    "warn_mm": 5.0,
    "pass_pct": None,
    "warn_pct": None,
}


def load_json(path: str | Path) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def write_json(path: str | Path, obj: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, allow_nan=False)
        f.write("\\n")


def _scene_to_mesh(path: str | Path) -> trimesh.Trimesh:
    """Load a GLB as a scene and bake scene-node transforms into geometry."""
    loaded = trimesh.load(str(path), force="scene", process=False)
    if isinstance(loaded, trimesh.Trimesh):
        meshes = [loaded]
    elif isinstance(loaded, trimesh.Scene):
        # dump(concatenate=True) applies graph transforms to each instance.
        dumped = loaded.dump(concatenate=True)
        meshes = [g for g in dumped if isinstance(g, trimesh.Trimesh)]
    else:
        raise ValueError(f"Unsupported file contents: {type(loaded).__name__}")

    meshes = [m.copy() for m in meshes if len(m.vertices) and len(m.faces)]
    if not meshes:
        raise ValueError("No triangle mesh geometry found in GLB.")
    # Keep disconnected objects together for global bounds/sections, but report components.
    combined = trimesh.util.concatenate(meshes)
    if not np.isfinite(combined.vertices).all():
        raise ValueError("Mesh contains non-finite vertex coordinates.")
    return combined


def _axis_vector(axis: str) -> np.ndarray:
    axis = axis.lower()
    if axis not in {"x", "y", "z", "-x", "-y", "-z"}:
        raise ValueError("axis must be one of x, y, z, -x, -y, -z")
    v = np.zeros(3, dtype=float)
    v[{"x": 0, "y": 0, "z": 2, "-x": 0, "-y": 1, "-z": 2}[axis]] = -1.0 if axis.startswith("-") else 1.0
    # Correct the negative X mapping (dict maps -x to index 0).
    return v


def _pca_axis(vertices: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    centered = vertices - vertices.mean(axis=0)
    cov = np.cov(centered, rowvar=False)
    vals, vecs = np.linalg.eigh(cov)
    order = np.argsort(vals)[::-1]
    vals, vecs = vals[order], vecs[:, order]
    axis = vecs[:, 0]
    axis /= np.linalg.norm(axis)
    return axis, vals


def _perimeter_of_path(path: trimesh.path.Path3D) -> tuple[float | None, int, bool]:
    """Return total perimeter only for exactly one closed path loop."""
    entities = list(path.entities)
    if not entities:
        return None, 0, False
    # Path3D.discrete yields one polyline per entity-connected component in most cases.
    discrete = path.discrete
    loops = []
    open_found = False
    for points in discrete:
        points = np.asarray(points)
        if len(points) < 3:
            open_found = True
            continue
        closed = np.linalg.norm(points[0] - points[-1]) <= 1e-5
        if not closed:
            open_found = True
            continue
        # Avoid duplicate final point; sum all consecutive edge lengths including closure.
        lengths = np.linalg.norm(np.diff(points, axis=0), axis=1)
        loops.append(float(lengths.sum()))
    if len(loops) != 1 or open_found:
        return None, len(loops), open_found
    return loops[0], len(loops), open_found


def _mesh_defects(mesh: trimesh.Trimesh) -> dict[str, Any]:
    try:
        components = mesh.split(only_watertight=False)
        component_count = len(components)
        component_faces = sorted((int(len(c.faces)) for c in components), reverse=True)
    except Exception:
        component_count, component_faces = None, []
    try:
        nonmanifold_edges = int(np.sum(mesh.edges_unique_length >= 0) - len(mesh.edges_unique))
        # trimesh doesn't expose a universally stable non-manifold-edge count across versions.
        # Use face-adjacency degree per unique edge as a robust count.
        edge_faces = mesh.edges_unique_inverse
        counts = np.bincount(edge_faces, minlength=len(mesh.edges_unique))
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
    submitted: dict[str, Any],
    *,
    mesh_units: str = "m",
    axis_mode: str = "pca",
    axis: str = "z",
    proximal_end: str = "min",
    tolerances: dict[str, Any] | None = None,
    spread_offset_pct: float = 2.0,
) -> dict[str, Any]:
    tol = {**DEFAULT_TOLERANCES, **(tolerances or {})}
    if mesh_units not in {"m", "mm", "cm"}:
        raise ValueError("mesh_units must be m, cm, or mm")
    if proximal_end not in {"min", "max"}:
        raise ValueError("proximal_end must be min or max")
    if axis_mode not in {"pca", "fixed"}:
        raise ValueError("axis_mode must be pca or fixed")

    mesh = _scene_to_mesh(glb_path)
    unit_to_mm = {"m": 1000.0, "cm": 10.0, "mm": 1.0}[mesh_units]
    # Transform coordinates to millimeters before computing dimensional values.
    mesh.apply_scale(unit_to_mm)
    vertices = np.asarray(mesh.vertices)

    pca_axis, eigenvalues = _pca_axis(vertices)
    if axis_mode == "fixed":
        long_axis = _axis_vector(axis)
        axis_source = f"configured fixed axis {axis}"
    else:
        long_axis = pca_axis
        axis_source = "principal component 1 (PCA)"
    # PCA sign is arbitrary. Establish direction with fixed-axis config or submitted orientation.
    if axis_mode == "fixed":
        direction_axis = long_axis.copy()
    else:
        # Use global positive direction to make sign deterministic, then proximal_end chooses end.
        idx = int(np.argmax(np.abs(long_axis)))
        if long_axis[idx] < 0:
            long_axis = -long_axis
        direction_axis = long_axis.copy()

    projected = vertices @ long_axis
    pmin, pmax = float(projected.min()), float(projected.max())
    length_mm = pmax - pmin
    if length_mm <= 0:
        raise ValueError("Mesh has zero or invalid extent along selected axis.")
    proximal_projection = pmin if proximal_end == "min" else pmax
    # Orient the station coordinate so distance 0 is proximal and 100% is distal.
    oriented_distance = projected - pmin if proximal_end == "min" else pmax - projected
    center = vertices.mean(axis=0)
    # A plane normal is the selected axis. Center it at each target projection.
    submitted_length = submitted.get("length_mm")
    length_diff = None
    length_pct = None
    if isinstance(submitted_length, (int, float)) and submitted_length != 0:
        length_diff = length_mm - float(submitted_length)
        length_pct = 100.0 * length_diff / float(submitted_length)

    length_status = "not_verified" if submitted_length is None else _status(abs(length_diff), tol)
    stations_in = submitted.get("stations", [])
    station_results = []
    not_verified = []
    for target_pct in (25, 50, 75):
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
        station_distance = length_mm * target_pct / 100.0
        # Position along oriented axis measured from the proximal endpoint.
        station_projection = pmin + station_distance if proximal_end == "min" else pmax - station_distance
        plane_origin = center + long_axis * (station_projection - float(center @ long_axis))
        section = mesh.section(plane_origin=plane_origin, plane_normal=long_axis)
        measured_c, loop_count, open_loop = (None, 0, False) if section is None else _perimeter_of_path(section)
        spread_values = []
        spread_notes = []
        for offset in (-spread_offset_pct, spread_offset_pct):
            d = length_mm * (target_pct + offset) / 100.0
            if d <= 0 or d >= length_mm:
                continue
            pr = pmin + d if proximal_end == "min" else pmax - d
            origin = center + long_axis * (pr - float(center @ long_axis))
            sec = mesh.section(plane_origin=origin, plane_normal=long_axis)
            perim, loops, opened = (None, 0, False) if sec is None else _perimeter_of_path(sec)
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
        pct = 100.0 * diff / submitted_c if submitted_c else None
        station_results.append({
            "name": name, "submitted_mm": submitted_c, "measured_mm": measured_c,
            "diff_mm": diff, "diff_pct": pct, "status": _status(abs(diff), tol),
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
    elif "flagged" in comparisons or not_verified or not defects["is_watertight"] or (defects["nonmanifold_edge_count"] or 0) > 0:
        overall = "warn"
    else:
        overall = "pass"
    if submitted_length is None:
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


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("glb", help="Input GLB file")
    p.add_argument("submitted_json", help="JSON file with length_mm and stations")
    p.add_argument("-o", "--output", default="measurement_report.json")
    p.add_argument("--mesh-units", choices=["m", "cm", "mm"], default="m",
                   help="Units of vertex coordinates before conversion (default: m, glTF convention)")
    p.add_argument("--axis-mode", choices=["pca", "fixed"], default="pca")
    p.add_argument("--axis", choices=["x", "y", "z", "-x", "-y", "-z"], default="z",
                   help="Used only with --axis-mode fixed")
    p.add_argument("--proximal-end", choices=["min", "max"], default="min",
                   help="Which end along the selected axis is proximal")
    p.add_argument("--pass-mm", type=float, default=2.0)
    p.add_argument("--warn-mm", type=float, default=5.0)
    p.add_argument("--spread-offset-pct", type=float, default=2.0)
    args = p.parse_args()
    submitted = load_json(args.submitted_json)
    report = evaluate(args.glb, submitted, mesh_units=args.mesh_units,
                      axis_mode=args.axis_mode, axis=args.axis,
                      proximal_end=args.proximal_end,
                      tolerances={"pass_mm": args.pass_mm, "warn_mm": args.warn_mm},
                      spread_offset_pct=args.spread_offset_pct)
    write_json(args.output, report)
    print(report["summary"])
    print(f"Report written to: {args.output}")


if __name__ == "__main__":
    main()
