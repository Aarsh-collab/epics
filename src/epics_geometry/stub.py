"""Scale an isolated TRELLIS stub GLB to seven human measurements.

Pipeline: load a static GLB with scene transforms applied, detect the planar
base cutoff and long axis, scale uniformly to the measured length, then stretch
cross-sections about the centreline so that circumference and maximum width
match at 25/50/75% of the length. The exported GLB is reopened and measured
independently before it is given its final name.

GLB coordinates are metres; measurements and reports are millimetres. Matching
the entered values is a software check, not a clinical accuracy claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from importlib import metadata
from pathlib import Path

import numpy as np
import scipy
import trimesh
from scipy.interpolate import PchipInterpolator
from scipy.optimize import least_squares
from scipy.spatial.distance import pdist

from . import stub_geometry as sg
from .errors import StubError
from .glb_io import load_instances, read_glb_json
from .measurements import (
    STATION_FRACTIONS,
    MeasurementError,
    StubMeasurements,
    load_measurements,
    match_tolerance_mm,
    parse_measurements,
)

LIMITATIONS = [
    "Only the seven entered measurements are matched; shape between and beyond "
    "the stations is the generator's inferred geometry, smoothly adjusted.",
    "Base to 25% and 75% to tip keep the nearest station's correction; those "
    "regions were not measured.",
    "Measurements do not record which direction the widest span points; the "
    "scaler stretches along the mesh's own principal transverse directions.",
    "Circumference is the convex outline of each section (taut-tape analogue).",
    "This is reference geometry for device design, not a medical-grade or "
    "verified anatomical measurement. Human review is required before use.",
]


@dataclass(frozen=True)
class FitLimits:
    """Largest transverse change the scaler may apply after length scaling."""

    min_factor: float = 0.70
    max_factor: float = 1.43
    max_anisotropy: float = 1.35  # max ratio between the two factors at a station
    max_iterations: int = 8


# ------------------------------------------------------------------ measuring


def _analyze(path: Path):
    instances, info = load_instances(path)
    topo = sg.build_topology(instances)
    alignment = sg.detect_alignment(topo)
    return instances, info, topo, alignment


def _measure_canonical(vc: np.ndarray, f: np.ndarray, length: float) -> list[sg.Section]:
    secs = []
    for fr in STATION_FRACTIONS:
        where = f"at {fr:.0%} ({fr * length:.1f} mm from base)"
        sec = sg.canonical_section(vc, f, fr * length, where)
        sg.check_convex(sec, where)
        secs.append(sec)
    return secs


def measure_glb(path: Path) -> dict:
    """Measure a GLB (coordinates taken as metres) with the seven definitions.

    Alignment is detected from the file itself, so this can independently check
    any exported mesh. Values are millimetres.
    """
    path = Path(path)
    _, _, topo, al = _analyze(path)
    vc = al.canonical(topo.vertices) * 1000.0
    length = float(vc[:, 2].max())
    prof = sg.profile(vc, topo.faces, length)
    sg.check_straight(prof)
    secs = _measure_canonical(vc, topo.faces, length)
    return {
        "glb": str(path),
        "units": "mm (GLB coordinates read as metres)",
        "length_mm": length,
        "sections": [
            {
                "fraction": fr,
                "distance_from_base_mm": s.z,
                "circumference_mm": s.circumference,
                "max_width_mm": s.max_width,
                "contour_perimeter_mm": s.contour_perimeter,
                "contour_excess": s.contour_excess,
                "solidity": s.solidity,
            }
            for fr, s in zip(STATION_FRACTIONS, secs)
        ],
        "alignment": al.summary(),
        "frame_check": {
            "base_normal_vs_file_z_deg": math.degrees(math.acos(min(1.0, abs(al.rotation[2, 2])))),
            "base_origin_mm": (al.origin * 1000.0).tolist(),
        },
        "straightness": {"lean_deg": prof.lean_deg, "bend_fraction_of_length": prof.bend},
    }


# ------------------------------------------------------------------ fitting


def _section_metrics(hull: np.ndarray, s: np.ndarray) -> tuple[float, float]:
    q = hull * s
    return sg.closed_perimeter(q), float(pdist(q).max())


def _fit_station(hull: np.ndarray, circ: float, width: float, where: str) -> np.ndarray:
    """Positive (sx, sy) so the scaled hull matches both targets, least distortion."""

    def residual(s):
        p, w = _section_metrics(hull, s)
        return [p / circ - 1.0, w / width - 1.0]

    starts = (0.6, 0.85, 1.15, 1.6)
    found, best = [], (math.inf, None)
    for a in starts:
        for b in starts:
            r = least_squares(
                residual, [a, b], bounds=([0.25, 0.25], [4.0, 4.0]), xtol=1e-12, ftol=1e-12, gtol=1e-12
            )
            err = float(np.max(np.abs(r.fun)))
            if err < best[0]:
                best = (err, r.x)
            if err <= 2e-4:
                found.append(r.x)
    if not found:
        raise StubError(
            "fit_failed",
            f"no transverse stretch of the generated section {where} matches "
            f"circumference {circ:.1f} mm and width {width:.1f} mm together "
            f"(closest relative error {100 * best[0]:.2f}%). Check the measurements "
            "or the generated shape.",
            {"closest_factors": [float(x) for x in best[1]]},
        )
    sols = np.unique(np.round(np.array(found), 6), axis=0)
    distortion = (np.log(sols) ** 2).sum(axis=1)
    # Near-circular sections give mirror solutions of equal distortion; prefer
    # stretching along X, the mesh's own wider transverse direction.
    order = np.lexsort((-(sols[:, 0] - sols[:, 1]), np.round(distortion, 9)))
    return sols[order[0]]


def _check_limits(factors: np.ndarray, limits: FitLimits) -> None:
    for fr, (sx, sy) in zip(STATION_FRACTIONS, factors):
        aniso = max(sx / sy, sy / sx)
        if not (limits.min_factor <= sx <= limits.max_factor and limits.min_factor <= sy <= limits.max_factor):
            raise StubError(
                "fit_failed",
                f"matching the section at {fr:.0%} needs transverse factors "
                f"({sx:.3f}, {sy:.3f}), outside the allowed [{limits.min_factor}, "
                f"{limits.max_factor}]. The generated shape differs too much from the "
                "measurements; check them or regenerate the mesh.",
                {"factors": factors.tolist()},
            )
        if aniso > limits.max_anisotropy:
            raise StubError(
                "fit_failed",
                f"matching the section at {fr:.0%} needs a {aniso:.2f}:1 stretch "
                f"between directions (limit {limits.max_anisotropy}:1).",
                {"factors": factors.tolist()},
            )


class _Deformer:
    """Transverse scaling about the centreline, smooth along the length."""

    def __init__(self, centre_z, centres, station_z, factors):
        self.cz = (centre_z.min(), centre_z.max())
        self.centre = PchipInterpolator(centre_z, centres, axis=0)
        self.sz = (station_z.min(), station_z.max())
        self.factor = PchipInterpolator(station_z, factors, axis=0)

    def factors_at(self, z: np.ndarray) -> np.ndarray:
        # Hold the nearest station's correction beyond the measured range.
        return self.factor(np.clip(z, *self.sz))

    def __call__(self, vc: np.ndarray) -> np.ndarray:
        z = vc[:, 2]
        c = self.centre(np.clip(z, *self.cz))
        s = self.factors_at(z)
        out = vc.copy()
        out[:, :2] = c + s * (vc[:, :2] - c)
        return out


def _vertex_normals(v: np.ndarray, f: np.ndarray) -> np.ndarray:
    cross = np.cross(v[f[:, 1]] - v[f[:, 0]], v[f[:, 2]] - v[f[:, 0]])
    n = np.zeros_like(v)
    for k in range(3):
        np.add.at(n, f[:, k], cross)
    norm = np.linalg.norm(n, axis=1)
    n[norm == 0] = (0.0, 0.0, 1.0)
    return n / np.where(norm > 0, norm, 1.0)[:, None]


# ------------------------------------------------------------------ main operation


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _versions() -> dict:
    try:
        pkg = metadata.version("epics-geometry")
    except metadata.PackageNotFoundError:
        pkg = "unknown"
    return {
        "epics_geometry": pkg,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "scipy": scipy.__version__,
        "trimesh": trimesh.__version__,
    }


def default_report_path(output_path: Path) -> Path:
    return output_path.with_name(output_path.stem + ".report.json")


def _comparison(targets: StubMeasurements, measured: dict) -> list[dict]:
    pairs = [("length", targets.length_mm, measured["length_mm"])]
    for t, m in zip(targets.sections, measured["sections"]):
        pct = f"{round(t.fraction * 100)}"
        pairs.append((f"circumference_{pct}", t.circumference_mm, m["circumference_mm"]))
        pairs.append((f"max_width_{pct}", t.max_width_mm, m["max_width_mm"]))
    rows = []
    for name, target, actual in pairs:
        err = actual - target
        tol = match_tolerance_mm(target)
        rows.append(
            {
                "measurement": name,
                "target_mm": round(target, 4),
                "actual_mm": round(actual, 4),
                "abs_error_mm": round(abs(err), 4),
                "error_pct": round(100 * err / target, 4),
                "tolerance_mm": round(tol, 4),
                "within_tolerance": bool(abs(err) <= tol),
            }
        )
    return rows


def scale_stub(
    input_path: Path,
    measurements: StubMeasurements | dict | Path,
    output_path: Path,
    *,
    report_path: Path | None = None,
    overwrite: bool = False,
    limits: FitLimits = FitLimits(),
) -> dict:
    """Scale a stub GLB to the seven measurements and return the report.

    The report is always written. Its ``status`` is ``measurements_matched``
    only when the exported file, reopened and re-measured, is within tolerance
    for every value; any other status means no output GLB was written.
    Raises ``FileExistsError``/``ValueError``/``FileNotFoundError`` for path
    problems before any work is done.
    """
    input_path, output_path = Path(input_path), Path(output_path)
    report_path = Path(report_path) if report_path else default_report_path(output_path)
    if not input_path.is_file():
        raise FileNotFoundError(input_path)
    if input_path.suffix.lower() != ".glb" or output_path.suffix.lower() != ".glb":
        raise ValueError("input and output must both be .glb files")
    if report_path.suffix.lower() != ".json":
        raise ValueError("report path must be a .json file")
    temp_path = output_path.with_name(f".{output_path.stem}.partial.glb")
    paths = {
        "input": input_path.resolve(),
        "output": output_path.resolve(),
        "report": report_path.resolve(),
        "temporary output": temp_path.resolve(),
    }
    if len(set(paths.values())) != len(paths):
        raise ValueError(f"input, output, report and temporary paths must all differ: {paths}")
    for p in (output_path, report_path):
        if p.is_dir():
            raise ValueError(f"{p} is a directory")
        if p.exists() and not overwrite:
            raise FileExistsError(f"{p} exists; pass overwrite=True / --overwrite to replace it")
    if overwrite:
        # Remove the previous run's files up front so a failed or interrupted
        # run can never leave an old mesh or report next to the new result.
        output_path.unlink(missing_ok=True)
        report_path.unlink(missing_ok=True)

    timing, t_start = {}, time.perf_counter()
    input_hash = _sha256(input_path)
    report: dict = {
        "status": None,
        "message": None,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "software": _versions(),
        "input": {"glb": str(input_path), "sha256": input_hash},
        "output": {"glb": None, "units": "metres", "frame": "base plane at Z=0, tip toward +Z"},
        "report_units": "mm",
        "tolerance_rule": "each value within max(0.5 mm, 1% of target); software matching "
        "threshold only, not a clinical accuracy claim",
        "limits": asdict(limits),
        "warnings": [],
        "limitations": LIMITATIONS,
    }

    def lap(name, t0):
        timing[name] = round(time.perf_counter() - t0, 3)
        return time.perf_counter()

    try:
        t = time.perf_counter()
        try:
            if isinstance(measurements, StubMeasurements):
                m = measurements
            elif isinstance(measurements, dict):
                m = parse_measurements(measurements)
            else:
                m = load_measurements(Path(measurements))
        except (MeasurementError, OSError) as exc:
            raise StubError("invalid_measurements", str(exc)) from exc
        report["measurement_input"] = m.to_dict()
        report["station_distances_mm"] = m.station_distances_mm()

        instances, info, topo, al = _analyze(input_path)
        report["input"].update(info)
        report["warnings"] += info["warnings"] + topo.warnings
        report["alignment"] = al.summary()
        report["alignment"]["length_source_units"] = al.length
        t = lap("load_and_align", t)

        L = m.length_mm
        scale = L / al.length  # mm per source unit
        vc = al.canonical(topo.vertices) * scale
        f = topo.faces
        prof = sg.profile(vc, f, L)
        sg.check_straight(prof)
        report["alignment"]["straightness"] = {
            "lean_deg": round(prof.lean_deg, 3),
            "bend_fraction_of_length": round(prof.bend, 5),
        }
        before = _measure_canonical(vc, f, L)
        report["after_length_scaling_mm"] = [
            {"fraction": fr, "circumference_mm": round(s.circumference, 3), "max_width_mm": round(s.max_width, 3)}
            for fr, s in zip(STATION_FRACTIONS, before)
        ]
        matrix = np.eye(4)
        matrix[:3, :3] = al.rotation * (scale / 1000.0)
        matrix[:3, 3] = -(al.rotation @ al.origin) * (scale / 1000.0)
        report["alignment"]["source_to_output_matrix_before_transverse_fit"] = matrix.tolist()

        centre_z = np.array([s.z for s in prof.sections])
        centres = np.array([s.centroid for s in prof.sections])
        station_z = np.array([s.z for s in before])
        targets_c = np.array([s.circumference_mm for s in m.sections])
        targets_w = np.array([s.max_width_mm for s in m.sections])
        hulls = [s.hull - s.centroid for s in before]
        tols = np.array([match_tolerance_mm(x) for x in (*targets_c, *targets_w)])

        adj_c, adj_w = targets_c.copy(), targets_w.copy()
        converged = False
        for iteration in range(1, limits.max_iterations + 1):
            factors = np.array(
                [
                    _fit_station(h, c, w, f"at {fr:.0%}")
                    for h, c, w, fr in zip(hulls, adj_c, adj_w, STATION_FRACTIONS)
                ]
            )
            _check_limits(factors, limits)
            deform = _Deformer(centre_z, centres, station_z, factors)
            deformed = deform(vc)
            now = [sg.canonical_section(deformed, f, z, f"at {z:.1f} mm") for z in station_z]
            got_c = np.array([s.circumference for s in now])
            got_w = np.array([s.max_width for s in now])
            err = np.abs(np.concatenate([got_c - targets_c, got_w - targets_w]))
            if (err <= 0.1 * tols).all():
                converged = True
                break
            adj_c *= targets_c / got_c
            adj_w *= targets_w / got_w
        if not (err <= tols).all():
            raise StubError(
                "fit_failed",
                f"section fit did not converge in {limits.max_iterations} iterations "
                f"(largest remaining error {err.max():.2f} mm).",
                {"remaining_errors_mm": err.round(4).tolist()},
            )
        z_dense = np.linspace(0, L, 201)
        report["deformation"] = {
            "uniform_scale_mm_per_source_unit": scale,
            "transverse_axes": "canonical X = principal transverse direction of the mesh surface, Y = Z x X",
            "stations": [
                {"fraction": fr, "z_mm": round(float(z), 3), "factor_x": float(sx), "factor_y": float(sy)}
                for fr, z, (sx, sy) in zip(STATION_FRACTIONS, station_z, factors)
            ],
            "interpolation": "PCHIP (shape-preserving) between stations; nearest station's "
            "factors held from base to 25% and from 75% to tip",
            "centreline": "PCHIP through section centroids at 10-90% of length",
            "factor_profile_sampled": {
                "z_mm": z_dense[::20].round(2).tolist(),
                "factors": deform.factors_at(z_dense[::20]).round(5).tolist(),
            },
            "iterations": iteration,
            "converged_to_tenth_of_tolerance": converged,
            "in_memory_errors_mm": err.round(4).tolist(),
        }
        t = lap("fit", t)

        normals = _vertex_normals(deformed, f)
        scene = trimesh.Scene()
        used = set()
        for inst, idx in zip(instances, topo.instance_index):
            name = inst.geometry
            while name in used:
                name += "_"
            used.add(name)
            mesh = trimesh.Trimesh(
                vertices=deformed[idx] / 1000.0,
                faces=inst.faces,
                visual=inst.visual,
                vertex_normals=normals[idx],
                process=False,
            )
            scene.add_geometry(mesh, geom_name=name, node_name=inst.node)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_bytes(scene.export(file_type="glb", include_normals=True))
        t = lap("export", t)

        out_doc = read_glb_json(temp_path)
        lost = info["images"] - len(out_doc.get("images") or [])
        if lost > 0:
            raise StubError("export_mismatch", f"{lost} texture image(s) were lost on re-export")
        _check_uvs(instances, temp_path)
        measured = measure_glb(temp_path)
        rows = _comparison(m, measured)
        report["results"] = rows
        report["verification"] = {
            "method": "exported GLB reopened; alignment re-detected and all seven values re-measured",
            "base_normal_vs_file_z_deg": round(measured["frame_check"]["base_normal_vs_file_z_deg"], 4),
            "uv_coordinates_preserved": True,
            "texture_images": len(out_doc.get("images") or []),
        }
        t = lap("verify", t)
        if not all(r["within_tolerance"] for r in rows):
            bad = [r["measurement"] for r in rows if not r["within_tolerance"]]
            raise StubError("export_mismatch", f"exported mesh misses tolerance for {bad}")

        os.replace(temp_path, output_path)
        report["output"]["glb"] = str(output_path)
        report["output"]["sha256"] = _sha256(output_path)
        report["status"] = "measurements_matched"
        report["message"] = "all seven exported measurements are within tolerance"
    except StubError as exc:
        report["status"] = exc.code
        report["message"] = exc.message
        if exc.details:
            report["details"] = exc.details
        temp_path.unlink(missing_ok=True)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        timing["total"] = round(time.perf_counter() - t_start, 3)
        report["timing_s"] = timing

    after_hash = _sha256(input_path)
    report["input"]["unchanged"] = after_hash == input_hash
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, default=_json_default) + "\n")
    report["report_path"] = str(report_path)
    return report


def _check_uvs(instances, path: Path) -> None:
    exported, _ = load_instances(path)
    source = {i.node: i for i in instances}
    for inst in exported:
        src = source.get(inst.node)
        src_uv = getattr(src.visual, "uv", None) if src else None
        if src_uv is None:
            continue
        out_uv = getattr(inst.visual, "uv", None)
        if out_uv is None or out_uv.shape != src_uv.shape or not np.allclose(out_uv, src_uv, atol=1e-6):
            raise StubError("export_mismatch", f"texture coordinates changed for node {inst.node!r}")


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, np.ndarray):
        return o.tolist()
    raise TypeError(type(o).__name__)


# ------------------------------------------------------------------ CLIs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="epics-scale-stub",
        description="Scale an isolated stub GLB to seven measurements (mm).",
    )
    parser.add_argument("input_glb", type=Path)
    parser.add_argument("--measurements", type=Path, required=True, help="version-1 measurements JSON")
    parser.add_argument("--output", type=Path, required=True, help="scaled GLB to write (metres)")
    parser.add_argument("--report", type=Path, help="report JSON (default: <output>.report.json)")
    parser.add_argument("--overwrite", action="store_true", help="delete existing output/report first, then write new ones")
    d = FitLimits()
    parser.add_argument("--min-factor", type=float, default=d.min_factor)
    parser.add_argument("--max-factor", type=float, default=d.max_factor)
    parser.add_argument("--max-anisotropy", type=float, default=d.max_anisotropy)
    args = parser.parse_args(argv)
    limits = FitLimits(args.min_factor, args.max_factor, args.max_anisotropy)
    try:
        report = scale_stub(
            args.input_glb,
            args.measurements,
            args.output,
            report_path=args.report,
            overwrite=args.overwrite,
            limits=limits,
        )
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"status: {report['status']}\n{report['message']}")
    for r in report.get("results", []):
        mark = "ok " if r["within_tolerance"] else "OUT"
        print(
            f"  {mark} {r['measurement']:<18} target {r['target_mm']:8.2f}  "
            f"actual {r['actual_mm']:8.2f}  error {r['abs_error_mm']:.2f} mm"
        )
    print(f"report: {report['report_path']}")
    if report["output"]["glb"]:
        print(f"output: {report['output']['glb']}")
    return 0 if report["status"] == "measurements_matched" else 1


def measure_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="epics-measure-stub",
        description="Independently measure a stub GLB (metres) with the seven definitions.",
    )
    parser.add_argument("glb", type=Path)
    args = parser.parse_args(argv)
    try:
        result = measure_glb(args.glb)
    except StubError as exc:
        print(json.dumps({"status": exc.code, "message": exc.message, **exc.details}, indent=2))
        return 1
    print(json.dumps(result, indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    sys.exit(main())
