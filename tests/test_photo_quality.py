import io
import json

import numpy as np
import pytest
from PIL import Image, ImageFilter

from epics_geometry.photo_prep import png_bytes
from epics_geometry.photo_quality import evaluate, decision

START, TIP = {"x": .25, "y": .5}, {"x": .85, "y": .5}


def scene():
    rng = np.random.default_rng(42)
    pixels = np.full((400, 600, 3), 90, dtype=np.uint8)
    mask = np.zeros((400, 600))
    mask[100:300, :520] = 1
    texture = rng.integers(90, 180, (400, 600), dtype=np.uint8)
    pixels[mask > 0] = np.repeat(texture[..., None], 3, axis=2)[mask > 0]
    return Image.fromarray(pixels), mask


def assess(image, mask, **kwargs):
    return evaluate(png_bytes(image), mask=mask, start=START, tip=TIP, **kwargs)


def checks(report):
    return {c["code"]: c["status"] for c in report["checks"]}


def test_textured_foreground_passes_and_proximal_edge_is_allowed():
    image, mask = scene()
    report = assess(image, mask)
    assert all(c["status"] == "pass" for c in report["checks"]), report
    assert decision(report, "strict")["status"] == "pass"
    assert report["metrics"]["tip_touches_edge"] is False


def test_blur_is_measured_on_limb_interior_not_its_sharp_silhouette():
    image, mask = scene()
    blurred = image.filter(ImageFilter.GaussianBlur(5))
    assert checks(assess(blurred, mask))["blur"] == "warn"
    assert checks(assess(image, mask))["blur"] == "pass"


def test_smooth_surface_passes_but_defocus_warns():
    # Synthetic low-texture skin-like surface, not a real-photo calibration set.
    image, mask = scene()
    y, x = np.indices(mask.shape)
    rng = np.random.default_rng(17)
    gray = np.clip(135 + 15 * np.sin(x / 90) + 10 * np.cos(y / 80)
                   + rng.normal(0, .7, mask.shape), 0, 255).astype(np.uint8)
    pixels = np.array(image)
    pixels[mask > 0] = np.repeat(gray[..., None], 3, axis=2)[mask > 0]
    image = Image.fromarray(pixels)
    report = assess(image, mask)
    assert 4 < report["metrics"]["sharpness"] < 25
    assert decision(report, "strict")["status"] == "pass", report
    for radius in (4, 10):
        assert checks(assess(image.filter(ImageFilter.GaussianBlur(radius)), mask))["blur"] == "warn"


@pytest.mark.parametrize("value", [25, 235])
@pytest.mark.parametrize("masked", [True, False])
def test_unclipped_dim_or_bright_image_warns_on_exposure(value, masked):
    image, mask = scene()
    pixels = np.array(image)
    pixels[:] = value
    report = assess(Image.fromarray(pixels), mask if masked else None)
    assert report["metrics"]["clipped_share"] == 0
    assert report["metrics"]["median_brightness"] == value
    assert checks(report)["exposure"] == "warn"


def test_darkened_textured_limb_warns_independently_of_blur():
    image, mask = scene()
    dark = Image.fromarray((np.asarray(image) * .15).astype(np.uint8))
    report = assess(dark, mask)
    assert report["metrics"]["clipped_share"] == 0
    assert checks(report)["blur"] == "pass"
    assert checks(report)["exposure"] == "warn"


@pytest.mark.parametrize("value", [0, 255])
def test_lost_exposure_detail_is_flagged(value):
    image, mask = scene()
    array = np.array(image)
    array[mask > 0] = value
    assert checks(assess(Image.fromarray(array), mask))["exposure"] == "warn"


def test_tip_at_edge_is_flagged_and_not_fixed_by_proximal_cut():
    image, mask = scene()
    mask[100:300, :] = 1
    report = assess(image, mask)
    assert checks(report)["framing"] == "warn"
    assert report["metrics"]["tip_touches_edge"] is True


def test_small_retained_limb_has_resolution_and_framing_flags():
    image, mask = scene()
    mask[:] = 0
    mask[180:220, 280:320] = 1
    report = assess(image, mask)
    assert checks(report)["resolution"] == checks(report)["framing"] == "warn"


def test_uncertain_and_fragmented_masks_are_reported():
    image, mask = scene()
    assert checks(assess(image, mask * .6))["segmentation"] == "warn"
    mask[320:390, 400:590] = 1
    report = assess(image, mask)
    assert checks(report)["segmentation"] == checks(report)["clutter"] == "warn"


def test_no_mask_does_not_claim_limb_checks_passed():
    image, _ = scene()
    report = evaluate(png_bytes(image))
    assert report["metrics"]["region"] == "whole_image"
    assert checks(report)["segmentation"] == "unavailable"
    assert decision(report, "soft")["status"] == "warn"
    assert decision(report, "strict")["status"] == "block"
    json.dumps(report, allow_nan=False)


def test_empty_mask_has_no_nonfinite_metrics():
    image, mask = scene()
    report = assess(image, mask * 0)
    assert checks(report)["blur"] == "unavailable"
    json.dumps(report, allow_nan=False)


@pytest.mark.parametrize("bad", [float("nan"), -1, 2])
def test_invalid_landmarks_and_masks_are_rejected(bad):
    image, mask = scene()
    with pytest.raises(ValueError):
        evaluate(png_bytes(image), start={"x": bad, "y": .5}, tip=TIP)
    mask[0, 0] = bad
    with pytest.raises(ValueError):
        assess(image, mask)


def test_exif_rotation_uses_oriented_mask_dimensions():
    image, mask = scene()
    exif = Image.Exif(); exif[274] = 6
    buffer = io.BytesIO(); image.save(buffer, format="JPEG", exif=exif)
    report = evaluate(buffer.getvalue(), mask=np.rot90(mask, -1))
    assert report["metrics"]["image_size_px"] == [400, 600]
    with pytest.raises(ValueError):
        evaluate(buffer.getvalue(), mask=mask)
