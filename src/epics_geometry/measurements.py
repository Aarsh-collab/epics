"""Versioned human-measurement input for stub scaling.

Version 1 contains seven millimetre values: one overall length plus the tape
circumference and caliper maximum width at 25%, 50% and 75% of that length,
measured from the selected base.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = 1
STATION_FRACTIONS = (0.25, 0.50, 0.75)

_TOP_LEVEL_KEYS = {"schema_version", "units", "length_mm", "sections", "notes"}
_SECTION_KEYS = {"fraction", "circumference_mm", "max_width_mm"}


class MeasurementError(ValueError):
    """The measurement input is missing, malformed or self-contradictory."""


@dataclass(frozen=True)
class SectionMeasurement:
    fraction: float
    circumference_mm: float
    max_width_mm: float


@dataclass(frozen=True)
class StubMeasurements:
    length_mm: float
    sections: tuple[SectionMeasurement, ...]

    def station_distances_mm(self) -> list[float]:
        return station_distances_mm(self.length_mm)

    def to_dict(self) -> dict:
        return {
            "schema_version": SCHEMA_VERSION,
            "units": "mm",
            "length_mm": self.length_mm,
            "sections": [
                {
                    "fraction": s.fraction,
                    "circumference_mm": s.circumference_mm,
                    "max_width_mm": s.max_width_mm,
                }
                for s in self.sections
            ],
        }


def station_distances_mm(length_mm: float) -> list[float]:
    """Physical distances from the base where the three sections are measured."""
    length_mm = _positive(length_mm, "length_mm")
    return [f * length_mm for f in STATION_FRACTIONS]


def match_tolerance_mm(target_mm: float) -> float:
    """Software matching threshold: the greater of 0.5 mm or 1% of the target.

    This decides whether the exported mesh reproduces the entered values; it is
    not a clinical accuracy claim.
    """
    return max(0.5, 0.01 * abs(target_mm))


def _positive(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MeasurementError(f"{name} must be a number in millimetres, got {value!r}")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise MeasurementError(f"{name} must be a positive finite number, got {value!r}")
    return value


def parse_measurements(data: Mapping[str, Any]) -> StubMeasurements:
    """Validate a version-1 measurement document and return it in station order."""
    if not isinstance(data, Mapping):
        raise MeasurementError("measurements must be a JSON object")
    unknown = set(data) - _TOP_LEVEL_KEYS
    if unknown:
        raise MeasurementError(f"unknown measurement fields: {sorted(unknown)}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise MeasurementError(
            f"schema_version must be {SCHEMA_VERSION}, got {data.get('schema_version')!r}"
        )
    if data.get("units", "mm") != "mm":
        raise MeasurementError("units must be 'mm'; convert all values to millimetres")
    if "length_mm" not in data:
        raise MeasurementError("length_mm is missing")
    length = _positive(data["length_mm"], "length_mm")
    if length > 2000:
        raise MeasurementError(f"length_mm {length} is implausibly large; check units are mm")

    raw_sections = data.get("sections")
    if not isinstance(raw_sections, list):
        raise MeasurementError("sections must be a list of three section records")

    by_fraction: dict[float, SectionMeasurement] = {}
    for i, raw in enumerate(raw_sections):
        if not isinstance(raw, Mapping):
            raise MeasurementError(f"sections[{i}] must be an object")
        missing = _SECTION_KEYS - set(raw)
        extra = set(raw) - _SECTION_KEYS
        if missing:
            raise MeasurementError(f"sections[{i}] is missing {sorted(missing)}")
        if extra:
            raise MeasurementError(f"sections[{i}] has unknown fields {sorted(extra)}")
        fraction = raw["fraction"]
        if isinstance(fraction, bool) or not isinstance(fraction, (int, float)):
            raise MeasurementError(f"sections[{i}].fraction must be a number")
        match = [f for f in STATION_FRACTIONS if math.isclose(fraction, f, abs_tol=1e-9)]
        if not match:
            raise MeasurementError(
                f"sections[{i}].fraction {fraction} is not one of {list(STATION_FRACTIONS)}"
            )
        f = match[0]
        if f in by_fraction:
            raise MeasurementError(f"fraction {f} appears more than once")
        by_fraction[f] = SectionMeasurement(
            fraction=f,
            circumference_mm=_positive(raw["circumference_mm"], f"sections[{i}].circumference_mm"),
            max_width_mm=_positive(raw["max_width_mm"], f"sections[{i}].max_width_mm"),
        )

    missing_fractions = [f for f in STATION_FRACTIONS if f not in by_fraction]
    if missing_fractions:
        raise MeasurementError(
            f"missing section measurements at fractions {missing_fractions}; "
            "all three stations are required for a complete scaling result"
        )

    sections = tuple(by_fraction[f] for f in STATION_FRACTIONS)
    for s in sections:
        _check_consistent(s, length)
    return StubMeasurements(length_mm=length, sections=sections)


def _check_consistent(s: SectionMeasurement, length_mm: float) -> None:
    """Reject circumference/width pairs no closed convex cross-section can have.

    For a convex closed curve with maximum width W, the perimeter C satisfies
    2W < C <= pi*W (equality for a circle). A taut tape follows the convex
    outline, so values outside that range are a measurement or entry error.
    """
    where = f"section at {s.fraction:.0%} ({s.fraction * length_mm:.1f} mm from base)"
    c, w = s.circumference_mm, s.max_width_mm
    upper = math.pi * w
    if c > upper + match_tolerance_mm(c):
        raise MeasurementError(
            f"{where}: circumference {c:g} mm exceeds pi x max width ({upper:.1f} mm). "
            "No closed cross-section has that combination; re-measure the "
            "circumference with the tape perpendicular to the stub and the width "
            "with a caliper across the widest straight span."
        )
    if c <= 2 * w:
        raise MeasurementError(
            f"{where}: circumference {c:g} mm is not more than twice the max width "
            f"({2 * w:.1f} mm). Check that the width is a straight-across caliper "
            "span and the circumference is a full loop."
        )


def load_measurements(path: Path) -> StubMeasurements:
    try:
        data = json.loads(Path(path).read_text())
    except json.JSONDecodeError as exc:
        raise MeasurementError(f"{path} is not valid JSON: {exc}") from exc
    return parse_measurements(data)
