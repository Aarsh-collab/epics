import csv

import numpy as np
from PIL import Image, ImageDraw

from epics_geometry.score_photos import BAD_VARIANTS, run


def synthetic(path):
    image = Image.new("RGB", (500, 280), (30, 60, 200))
    ImageDraw.Draw(image).rounded_rectangle((40, 80, 440, 200), radius=55, fill=(190, 140, 110))
    image.save(path)


def mask(image):
    pixels = np.asarray(image)
    return (pixels[..., 0] > pixels[..., 2]).astype(float)


def labels(folder, filenames=("good.png",)):
    fields = ["filename", "start_x", "start_y", "tip_x", "tip_y", "role", "pair",
              "human_ok", "human_problem", "deliberate"]
    with (folder / "labels.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for filename in filenames:
            writer.writerow(dict(filename=filename, start_x=.2, start_y=.5, tip_x=.88, tip_y=.5,
                                 role="main", pair="a", human_ok="yes", human_problem="", deliberate="no"))


def test_scores_csv_has_reports_and_refusals_do_not_crash(tmp_path):
    synthetic(tmp_path / "good.png")
    Image.new("RGB", (500, 280), "blue").save(tmp_path / "refused.png")
    labels(tmp_path, ("good.png", "refused.png"))
    scores, report = run(tmp_path, mask_fn=mask)
    with scores.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 2
    assert rows[0]["prep_status"] == "success"
    assert rows[1]["prep_status"] == "refused"
    for column in ("sharpness", "median_brightness", "blur_status", "no_mask_blur_status",
                   "width_05", "width_95", "quality_status"):
        assert column in rows[0]
    assert "Numeric metrics" in report.read_text()


def test_make_bad_copies_creates_expected_private_rows(tmp_path):
    synthetic(tmp_path / "good.png")
    labels(tmp_path)
    scores, _ = run(tmp_path, make_bad=True, mask_fn=mask)
    with scores.open(newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 1 + len(BAD_VARIANTS)
    assert all(row["human_ok"] == "no" and row["deliberate"] == "yes" for row in rows[1:])
    assert len(list((tmp_path / "output" / "bad_copies").glob("*.png"))) == len(BAD_VARIANTS)
