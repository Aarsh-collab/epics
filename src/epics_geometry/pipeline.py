"""Capture folder -> TRELLIS.2 GLB -> stub scaling -> independent validation.

Input is the folder the web app's export is copied into on Sol: ``capture.json``
plus the photos it names. Every run writes ``manifest.json`` in the output
folder, including runs that stop early, so each attempt can be logged on the
evaluation scorecard. A mesh is only listed as the result when the scaler's
status is ``measurements_matched``; it still needs human review before use.
This is reference geometry for device design, not a clinical measurement.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

from .measurements import MeasurementError, parse_measurements
from .stub import scale_stub
from . import validate

CAPTURE_FILE = "capture.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str) + "\n")


def read_capture(capture_dir: Path) -> tuple[dict, Path, dict]:
    """Return (capture record, generation photo path, stub measurements dict)."""
    capture_path = capture_dir / CAPTURE_FILE
    if not capture_path.is_file():
        raise FileNotFoundError(f"{capture_path} not found; export it from the web app")
    capture = json.loads(capture_path.read_text())
    if capture.get("schema_version") != "capture.v1":
        raise ValueError(f"{capture_path}: expected schema_version 'capture.v1'")
    photo = next((p for p in capture.get("photos", []) if p.get("role") == "generation"), None)
    if photo is None:
        raise ValueError(f"{capture_path}: no photo with role 'generation'")
    image = capture_dir / photo["filename"]
    if not image.is_file():
        raise FileNotFoundError(f"generation photo {image} not found; copy it next to {CAPTURE_FILE}")
    measurements = capture.get("stub_measurements")
    if measurements is None:
        raise MeasurementError(
            "capture has no complete stub_measurements (a station was marked unreliable or "
            "max widths are missing); it can be reviewed but not scaled"
        )
    return capture, image, measurements


def run(
    out_dir: Path,
    *,
    capture_dir: Path | None = None,
    image: Path | None = None,
    measurements: Path | dict | None = None,
    glb: Path | None = None,
    settings=None,
    outer_shell: bool = False,
    overwrite: bool = False,
) -> dict:
    """Run every stage and return the manifest (also written to ``out_dir``).

    Either ``capture_dir`` or ``measurements`` (plus ``image`` or ``glb``) must
    be given. Passing ``glb`` skips generation, so the CPU stages can run
    anywhere.
    """
    out_dir = Path(out_dir)
    if out_dir.exists() and any(out_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"{out_dir} is not empty; pass overwrite=True / --overwrite")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.json"
    manifest: dict = {
        "manifest_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "status": "running",
        "failed_stage": None,
        "stages": {},
        "result_glb": None,
        "review_status": "pending_human_review",
        "limitations": [
            "Reference geometry for device design only; not medically or metrically verified.",
            "Matching entered measurements is a software check, not a fit or safety assessment.",
            "Shape between and beyond the measured stations is the generator's inferred geometry.",
        ],
    }
    stage = "inputs"
    t_start = time.perf_counter()
    try:
        if capture_dir is not None:
            capture, capture_image, capture_measurements = read_capture(Path(capture_dir))
            image = image or capture_image
            measurements = measurements or capture_measurements
            manifest["capture"] = {"dir": str(capture_dir), "created_at": capture.get("created_at")}
        if measurements is None:
            raise ValueError("no measurements: pass a capture folder or a measurements JSON")
        if not isinstance(measurements, dict):
            measurements = json.loads(Path(measurements).read_text())
        parse_measurements(measurements)  # fail before spending GPU time
        _write_json(out_dir / "measurements.json", measurements)
        manifest["measurements"] = measurements
        if image is not None:
            manifest["input_photo"] = {"path": str(image), "sha256": _sha256(Path(image))}

        stage = "generate"
        if glb is None:
            if image is None:
                raise ValueError("no input: pass a capture folder, an image, or an existing GLB")
            from .trellis_run import TrellisSettings, generate_glb

            glb = out_dir / "generated.glb"
            manifest["stages"]["generate"] = generate_glb(Path(image), glb, settings or TrellisSettings())
        else:
            manifest["stages"]["generate"] = {"skipped": True, "glb": str(glb)}
        manifest["stages"]["generate"]["sha256"] = _sha256(Path(glb))

        stage = "outer_shell"
        to_scale = Path(glb)
        if outer_shell:
            from .outer_shell import extract

            shell_glb = out_dir / "outer_shell.glb"
            manifest["stages"]["outer_shell"] = extract(to_scale, shell_glb)
            to_scale = shell_glb

        stage = "scale"
        scaled = out_dir / "scaled.glb"
        report = scale_stub(to_scale, measurements, scaled, report_path=out_dir / "scale_report.json")
        manifest["stages"]["scale"] = {
            "status": report["status"], "message": report["message"], "report": str(out_dir / "scale_report.json"),
        }
        if report["status"] != "measurements_matched":
            manifest["status"] = report["status"]
            manifest["failed_stage"] = "scale"
            if "double-walled" in (report["message"] or "") and not outer_shell:
                manifest["hint"] = ("The generated mesh is a double-walled shell. Re-run with --outer-shell "
                                    "(team decision) or try generation with --no-remesh.")
            return manifest

        stage = "validate"
        validation = validate.evaluate(scaled, measurements, axis_mode="canonical")
        validate.write_json(out_dir / "validation.json", validation)
        manifest["stages"]["validate"] = {
            "overall_status": validation["overall_status"], "summary": validation["summary"],
            "report": str(out_dir / "validation.json"),
        }
        manifest["result_glb"] = {"path": str(scaled), "sha256": _sha256(scaled), "units": "m"}
        manifest["status"] = "complete" if validation["overall_status"] != "fail" else "validation_failed"
        if manifest["status"] == "validation_failed":
            manifest["failed_stage"] = "validate"
        return manifest
    except Exception as exc:
        manifest["status"] = "error"
        manifest["failed_stage"] = stage
        manifest["error"] = {"type": type(exc).__name__, "message": str(exc),
                             "traceback": traceback.format_exc(limit=8)}
        return manifest
    finally:
        manifest["elapsed_s"] = time.perf_counter() - t_start
        _write_json(manifest_path, manifest)


def main(argv: list[str] | None = None) -> int:
    from .trellis_run import add_settings_args, settings_from_args

    parser = argparse.ArgumentParser(prog="epics-pipeline", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("capture_dir", type=Path, nargs="?", help="folder with capture.json and photos")
    parser.add_argument("--out", type=Path, required=True, help="output folder")
    parser.add_argument("--image", type=Path, help="photo to generate from (overrides the capture)")
    parser.add_argument("--measurements", type=Path, help="stub_measurements v1 JSON (overrides the capture)")
    parser.add_argument("--glb", type=Path, help="existing GLB: skip TRELLIS.2 generation")
    parser.add_argument("--outer-shell", action="store_true",
                        help="keep only the outer wall of a nested double shell before scaling")
    parser.add_argument("--overwrite", action="store_true", help="allow a non-empty output folder")
    add_settings_args(parser)
    args = parser.parse_args(argv)
    if args.capture_dir is None and args.measurements is None:
        parser.error("give a capture folder or --measurements")
    try:
        manifest = run(args.out, capture_dir=args.capture_dir, image=args.image,
                       measurements=args.measurements, glb=args.glb,
                       settings=settings_from_args(args), outer_shell=args.outer_shell,
                       overwrite=args.overwrite)
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(f"status: {manifest['status']}")
    for name, stage in manifest["stages"].items():
        status = stage.get("status") or stage.get("overall_status") or ("skipped" if stage.get("skipped") else "done")
        print(f"  {name:<12} {status}")
    if manifest.get("error"):
        print(f"error in {manifest['failed_stage']}: {manifest['error']['message']}", file=sys.stderr)
    if manifest.get("hint"):
        print(f"hint: {manifest['hint']}")
    if manifest["result_glb"]:
        print(f"result: {manifest['result_glb']['path']} (pending human review)")
    print(f"manifest: {args.out / 'manifest.json'}")
    return 0 if manifest["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(main())
