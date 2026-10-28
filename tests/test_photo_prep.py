import io

import numpy as np
import pytest
from PIL import Image

from epics_geometry.photo_prep import PrepError, PrepSettings, png_bytes, prepare, preview

BACKGROUND = np.array([40, 60, 200])


def photo(size, a, b, radius, extra=None):
    """A textured limb from ``a`` (runs on past Start) to a rounded tip at ``b`` on a blue background."""
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w] + 0.5
    p = np.stack([xx, yy], -1)
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = b - a
    t = np.clip(((p - a) @ d) / (d @ d), 0, 1)
    dist = np.linalg.norm(p - (a + t[..., None] * d), axis=-1)
    limb = dist <= radius
    if extra is not None:
        limb |= np.linalg.norm(p - np.asarray(extra[0]), axis=-1) <= extra[1]
    img = np.empty((h, w, 3))
    img[:] = BACKGROUND
    # Skin with a gentle gradient and fine stripes, so copied texture and changed pixels are detectable.
    img[limb] = np.stack([190 + 30 * t, 140 + 10 * np.sin(xx / 3), 120 + 10 * np.cos(yy / 4)], -1)[limb]
    return Image.fromarray(img.astype(np.uint8)), limb


def mask_fn(image):
    rgb = np.asarray(image, dtype=float)
    return (np.abs(rgb - BACKGROUND).sum(axis=-1) > 60).astype(float)


def norm(pt, size):
    return {"x": pt[0] / size[0], "y": pt[1] / size[1]}


def run(size, a, b, radius, start, tip, settings=PrepSettings(), extra=None):
    img, limb = photo(size, a, b, radius, extra)
    return img, limb, prepare(png_bytes(img), norm(start, size), norm(tip, size), mask_fn, settings)


def along(size, start, tip):
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w] + 0.5
    s, t = np.asarray(start, float), np.asarray(tip, float)
    u = (t - s) / np.linalg.norm(t - s)
    return (np.stack([xx, yy], -1) - s) @ u


CASES = {
    "horizontal_landscape": ((400, 240), (-20, 120), (330, 120), 40, (100, 120), (370, 120)),
    "vertical_portrait": ((240, 400), (120, 420), (120, 70), 40, (120, 300), (120, 30)),
    "diagonal": ((400, 400), (-10, 390), (300, 80), 35, (110, 270), (325, 55)),
    "tip_left": ((400, 240), (420, 120), (70, 120), 40, (300, 120), (30, 120)),
}


@pytest.mark.parametrize("name", CASES)
@pytest.mark.parametrize("cap", ["straight", "oval"])
def test_cut_direction_and_length(name, cap):
    size, a, b, radius, start, tip = CASES[name]
    img, limb, prep = run(size, a, b, radius, start, tip, PrepSettings(cap=cap, pad=0))
    alpha = np.asarray(prep.rgba)[..., 3] / 255
    d = along(size, start, tip)
    assert alpha[d < -1].max() == 0  # nothing kept behind Start
    from scipy.ndimage import binary_erosion
    assert alpha[binary_erosion(limb) & (d > 3)].min() == 1  # limb on the Tip side kept
    kept = alpha > 0.5
    # The Start-to-Tip extent is unchanged: the cut sits at Start and the tip is untouched.
    assert d[kept].min() == pytest.approx(0, abs=1.0)
    assert d[kept].max() == pytest.approx(d[limb].max(), abs=1.0)
    assert prep.record["start_crossing_px"]["width"] == pytest.approx(2 * radius, abs=2)


@pytest.mark.parametrize("cap", ["straight", "oval"])
def test_retained_pixels_unchanged_outside_edge_and_face(cap):
    size, a, b, radius, start, tip = CASES["diagonal"]
    img, limb, prep = run(size, a, b, radius, start, tip, PrepSettings(cap=cap, pad=0))
    out = np.asarray(prep.rgba)
    alpha, face = out[..., 3], np.asarray(prep.face)
    interior = (alpha == 255) & (face == 0)
    assert np.array_equal(out[..., :3][interior], np.asarray(img)[interior])
    changed = (alpha > 0) & (alpha < 255)
    outline = np.ptp(np.argwhere(limb), axis=0).sum() * 2 + 2 * np.pi * radius
    assert changed.sum() <= 2 * outline  # one pixel row each side of the edge
    if cap == "straight":
        assert face.max() == 0


def test_oval_face_stays_on_the_retained_side_and_is_shallow():
    size, a, b, radius, start, tip = CASES["horizontal_landscape"]
    _, _, prep = run(size, a, b, radius, start, tip, PrepSettings(cap="oval", pad=0))
    face = np.asarray(prep.face) > 0
    d = along(size, start, tip)
    assert d[face].min() >= -0.5
    info = prep.record["face"]
    assert info["depth_px"] == pytest.approx(0.08 * info["width_px"])
    assert d[face].max() <= info["depth_px"] + info["join_px"] + 1
    # The face is lighter skin tone, not a dark band.
    rgb = np.asarray(prep.rgba)[..., :3][np.asarray(prep.face) == 255].astype(float)
    assert rgb.mean() > 150


def test_rotated_phone_photo_uses_display_orientation():
    size, a, b, radius, start, tip = CASES["vertical_portrait"]
    img, limb = photo(size, a, b, radius)
    stored = img.transpose(Image.Transpose.ROTATE_90)  # as a phone stores a portrait shot
    exif = Image.Exif()
    exif[0x0112] = 6  # display rotated 90 degrees clockwise
    buf = io.BytesIO()
    stored.save(buf, format="PNG", exif=exif)
    prep = prepare(buf.getvalue(), norm(start, size), norm(tip, size), mask_fn, PrepSettings(cap="straight", pad=0))
    assert prep.record["orientation"]["exif_orientation"] == 6
    assert prep.record["orientation"]["size"] == list(size)
    alpha = np.asarray(prep.rgba)[..., 3]
    assert alpha.shape == (size[1], size[0])
    assert alpha[along(size, start, tip) < -1].max() == 0


def test_tip_at_photo_edge_is_padded_with_a_warning():
    _, _, prep = run((400, 240), (-20, 120), (420, 120), 40, (100, 120), (399, 120), PrepSettings(pad=0.05))
    assert any("cannot be recovered" in w for w in prep.record["warnings"])
    assert prep.rgba.size == (440, 280)
    assert prep.record["transform"]["canvas_offset_px"] == [20, 20]
    assert np.asarray(prep.rgba)[..., 3][:20].max() == 0


def test_tip_in_the_middle_has_no_warning():
    size, a, b, radius, start, tip = CASES["horizontal_landscape"]
    _, _, prep = run(size, a, b, radius, start, tip)
    assert prep.record["warnings"] == []


def test_landmarks_too_close():
    with pytest.raises(PrepError, match="too close"):
        run((400, 240), (-20, 120), (330, 120), 40, (100, 120), (103, 121))


def test_start_off_the_limb():
    with pytest.raises(PrepError, match="Start"):
        run((400, 240), (150, 60), (330, 60), 30, (60, 200), (370, 200))


def test_other_large_object_is_ambiguous():
    with pytest.raises(PrepError, match="other large object"):
        run((400, 240), (-20, 80), (330, 80), 30, (100, 80), (370, 80), extra=((250, 200), 30))


def test_tip_on_a_different_object():
    with pytest.raises(PrepError, match="Tip is not on the limb"):
        run((400, 240), (-20, 120), (200, 120), 40, (100, 120), (380, 120))


def test_preview_on_grey_with_face_outline():
    size, a, b, radius, start, tip = CASES["horizontal_landscape"]
    _, _, prep = run(size, a, b, radius, start, tip, PrepSettings(cap="oval", pad=0))
    plain, marked = np.asarray(preview(prep)), np.asarray(preview(prep, outline=True))
    assert tuple(plain[0, 0]) == (128, 128, 128)
    assert (np.abs(plain.astype(int) - marked).sum(axis=-1) > 0).sum() > 0
