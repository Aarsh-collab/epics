"""Score a private folder of labelled photos for quality-gate calibration."""
from __future__ import annotations

import argparse
import csv
import math
import statistics
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from . import photo_prep, photo_quality, side_view

REQUIRED_COLUMNS = {
    "filename", "start_x", "start_y", "tip_x", "tip_y", "role",
    "pair", "human_ok", "human_problem", "deliberate",
}
BAD_VARIANTS = (
    ("blur2", "blurred (Gaussian radius 2)"),
    ("blur4", "blurred (Gaussian radius 4)"),
    ("blur8", "blurred (Gaussian radius 8)"),
    ("dark30", "darkened (brightness x0.3)"),
    ("dark15", "darkened (brightness x0.15)"),
    ("bright2", "brightened (brightness x2)"),
    ("bright3", "brightened (brightness x3)"),
)


def _point(row: dict, prefix: str) -> dict:
    point = {"x": float(row[f"{prefix}_x"]), "y": float(row[f"{prefix}_y"])}
    if not all(math.isfinite(v) and 0 <= v <= 1 for v in point.values()):
        raise ValueError(f"{prefix}_x and {prefix}_y must be numbers between 0 and 1")
    return point


def _flatten_report(target: dict, report: dict, prefix: str = "") -> None:
    for check in report["checks"]:
        target[f"{prefix}{check['code']}_status"] = check["status"]
        target[f"{prefix}{check['code']}_message"] = check["message"]
    for name, value in report["metrics"].items():
        if isinstance(value, (list, dict)):
            value = ";".join(map(str, value)) if isinstance(value, list) else str(value)
        target[f"{prefix}{name}"] = value


def _safe_photo(root: Path, filename: str) -> Path:
    path = (root / filename).resolve()
    if path.parent != root.resolve():
        raise ValueError("filename must name a photo directly inside the input folder")
    if not path.is_file():
        raise FileNotFoundError(f"photo not found: {filename}")
    return path


def _bad_copy(image: Image.Image, variant: str) -> Image.Image:
    if variant.startswith("blur"):
        return image.filter(ImageFilter.GaussianBlur(float(variant.removeprefix("blur"))))
    factors = {"dark30": .3, "dark15": .15, "bright2": 2, "bright3": 3}
    return ImageEnhance.Brightness(image).enhance(factors[variant])


def make_bad_copies(rows: list[dict], root: Path, out_dir: Path) -> list[dict]:
    """Create seven deliberately poor versions of every main photo under output/."""
    generated = []
    bad_dir = out_dir / "bad_copies"
    bad_dir.mkdir(parents=True, exist_ok=True)
    for index, row in enumerate(rows):
        if row.get("role", "").strip().lower() != "main":
            continue
        path = _safe_photo(root, row["filename"])
        with Image.open(path) as raw:
            image = ImageOps.exif_transpose(raw).convert("RGB")
        stem = "photo_" + str(index + 1)
        for variant, problem in BAD_VARIANTS:
            filename = f"{stem}__{variant}.png"
            destination = bad_dir / filename
            _bad_copy(image, variant).save(destination)
            copy = dict(row)
            copy.update(filename=f"bad_copies/{filename}", human_ok="no",
                        human_problem=problem, deliberate="yes", role="main",
                        _path=str(destination))
            generated.append(copy)
    return generated


def score_row(row: dict, root: Path, mask_fn=photo_prep.default_mask) -> dict:
    result = {k: v for k, v in row.items() if not k.startswith("_")}
    try:
        path = Path(row["_path"]) if row.get("_path") else _safe_photo(root, row["filename"])
        data = path.read_bytes()
        start, tip = _point(row, "start"), _point(row, "tip")
    except Exception as exc:
        result.update(prep_status="error", prep_message=str(exc), quality_status="unavailable",
                      profile_status="unavailable")
        return result

    no_mask = photo_quality.evaluate(data, start=start, tip=tip)
    _flatten_report(result, no_mask, "no_mask_")
    result["no_mask_quality_status"] = photo_quality.decision(no_mask, "strict")["status"]

    try:
        image, _ = photo_prep.oriented(data)
        alpha = np.clip(np.asarray(mask_fn(image), dtype=float), 0, 1)
        if alpha.shape != (image.height, image.width) or not np.isfinite(alpha).all():
            raise ValueError(f"mask is {alpha.shape}, photo is {(image.height, image.width)}")
    except Exception as exc:
        result.update(prep_status="unavailable", prep_message=f"Mask unavailable: {exc}",
                      quality_status="unavailable", profile_status="unavailable",
                      profile_message=str(exc))
        return result

    report = photo_quality.evaluate(data, mask=alpha, start=start, tip=tip)
    _flatten_report(result, report)
    result["quality_status"] = photo_quality.decision(report, "strict")["status"]
    fixed_mask = lambda _image: alpha
    try:
        prepared = photo_prep.prepare(
            data, start, tip, fixed_mask, photo_prep.PrepSettings(cap="straight")
        )
        result.update(
            prep_status="success",
            prep_message="",
            prep_warnings="; ".join(prepared.record["warnings"]),
            start_crossing_width_px=prepared.record["start_crossing_px"]["width"],
        )
    except photo_prep.PrepError as exc:
        result.update(prep_status="refused", prep_message=str(exc), prep_warnings="")
    except Exception as exc:
        result.update(prep_status="error", prep_message=str(exc), prep_warnings="")

    landmarks = {"model_start": start, "stub_endpoint": tip}
    try:
        profile = side_view.photo_profile(data, landmarks, fixed_mask)
        result.update(profile_status="success", profile_message="",
                      profile_length_px=profile["length_px"])
        for fraction, width in zip(side_view.FRACTIONS, profile["width_over_length"]):
            result[f"width_{round(100 * fraction):02d}"] = width
    except Exception as exc:
        result.update(profile_status="unavailable", profile_message=str(exc))
    return result


def _number(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def summary(rows: list[dict]) -> str:
    numeric = []
    for name in {key for row in rows for key in row}:
        values = [_number(row.get(name)) for row in rows]
        if name not in REQUIRED_COLUMNS and any(value is not None for value in values):
            numeric.append(name)
    good = [row for row in rows if row.get("human_ok", "").strip().lower() == "yes"]
    bad = [row for row in rows if row.get("human_ok", "").strip().lower() == "no"]

    def stats(group, name):
        values = [_number(row.get(name)) for row in group]
        values = [value for value in values if value is not None]
        if not values:
            return ("—", "—", "—", "0")
        return tuple(f"{value:.6g}" for value in (min(values), statistics.median(values), max(values))) + (str(len(values)),)

    lines = ["# Photo score summary", "", f"Photos: {len(rows)} total; {len(good)} human good; {len(bad)} human bad.", "",
             "## Numeric metrics", "", "| Metric | Good min | Good median | Good max | n | Bad min | Bad median | Bad max | n |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for name in sorted(numeric):
        lines.append("| " + " | ".join((name, *stats(good, name), *stats(bad, name))) + " |")
    false_warnings = [row["filename"] for row in good if row.get("quality_status") != "pass"]
    misses = [row["filename"] for row in bad if row.get("quality_status") == "pass"]
    lines.extend(["", "## Current-threshold errors", "",
                  f"False warnings/unavailable results: {len(false_warnings)} of {len(good)} human-good photos.",
                  f"Files: {', '.join(false_warnings) if false_warnings else 'none'}", "",
                  f"Misses: {len(misses)} of {len(bad)} human-bad photos.",
                  f"Files: {', '.join(misses) if misses else 'none'}", "",
                  "The thresholds are provisional; these counts need human review before changing them.", ""])
    return "\n".join(lines)


def run(folder: Path, output: Path | None = None, *, make_bad: bool = False,
        mask_fn=photo_prep.default_mask) -> tuple[Path, Path]:
    folder = Path(folder).resolve()
    labels = folder / "labels.csv"
    if not labels.is_file():
        raise FileNotFoundError(f"{labels} not found")
    with labels.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        missing = REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError("labels.csv is missing columns: " + ", ".join(sorted(missing)))
        source_rows = list(reader)
    out_dir = Path(output).resolve() if output else folder / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    rows = source_rows + (make_bad_copies(source_rows, folder, out_dir) if make_bad else [])
    scored = [score_row(row, folder, mask_fn) for row in rows]
    preferred = list(reader.fieldnames or [])
    fields = preferred + sorted({key for row in scored for key in row} - set(preferred))
    csv_path = out_dir / "scores.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(scored)
    summary_path = out_dir / "summary.md"
    summary_path.write_text(summary(scored), encoding="utf-8")
    return csv_path, summary_path


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path, help="folder containing labels.csv and its photos")
    parser.add_argument("--output", type=Path, help="output directory (default: FOLDER/output)")
    parser.add_argument("--make-bad-copies", action="store_true",
                        help="add blurred, darkened and brightened main-photo copies")
    parser.add_argument("--no-mask", action="store_true",
                        help="skip RMBG; still write whole-photo blur and exposure metrics")
    args = parser.parse_args(argv)
    mask_fn = (lambda _image: (_ for _ in ()).throw(RuntimeError("disabled by --no-mask"))) if args.no_mask else photo_prep.default_mask
    csv_path, summary_path = run(args.folder, args.output, make_bad=args.make_bad_copies, mask_fn=mask_fn)
    print(csv_path)
    print(summary_path)


if __name__ == "__main__":
    main()
