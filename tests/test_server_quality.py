import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from epics_geometry.photo_prep import png_bytes
from epics_geometry.server import create_app
from test_photo_quality import scene, START, TIP
from test_server import (PASSCODE, capture, client, stub_glb, login, png,
                         quality_token, targets_for, wait)


def post_job(client, record, *, ack="", photo=None, preparation=None):
    fields = {"capture": json.dumps(record), "quality_ack": ack}
    if preparation:
        fields["preparation"] = preparation
    return client.post("/api/jobs", data=fields, files=[
        ("photos", ("main.png", photo or png(), "image/png")),
        ("photos", ("side.png", png(), "image/png"))])


def test_quality_checks_require_login_and_do_not_store_uploads(client, tmp_path):
    assert client.post("/api/photo-quality", files={"photo": ("x.png", png())}).status_code == 401
    login(client)
    assert quality_token(client)
    assert not list((tmp_path / "data" / "jobs").iterdir())
    assert not list((tmp_path / "data" / "preps").iterdir())
    assert client.post("/api/photo-quality", files={"photo": ("x.png", b"bad")}).status_code == 400
    assert client.post("/api/photo-quality", data={"landmarks": "[]"}, files={"photo": ("x.png", png())}).status_code == 400


def test_soft_warnings_require_matching_ack_and_are_saved(client, stub_glb, tmp_path):
    login(client)
    record = capture(targets_for(stub_glb))
    # A client cannot replace the authoritative checks with its own passing report.
    record["photo_quality"] = {"status": "pass"}
    rejected = post_job(client, record)
    assert rejected.status_code == 422
    report = rejected.json()["photo_quality"]
    assert report["status"] == "warn"
    assert not list((tmp_path / "data" / "jobs").iterdir())
    accepted = post_job(client, record, ack=report["review_token"])
    assert accepted.status_code == 201, accepted.text
    job = wait(client, accepted.json()["id"])
    assert job["result"]["status"] == "complete"
    assert job["photo_quality"]["acknowledged"] is True
    assert job["photo_quality"]["calibration"] == "provisional"
    saved = json.loads((tmp_path / "data" / "jobs" / job["id"] / "input" / "capture.json").read_text())
    assert saved["photo_quality"] == job["photo_quality"]


def test_changed_photo_or_markers_invalidates_ack(client, stub_glb):
    login(client)
    record = capture(targets_for(stub_glb))
    ack = quality_token(client)
    changed = png_bytes(Image.new("RGB", (8, 8), "white"))
    assert post_job(client, record, ack=ack, photo=changed).status_code == 422
    record["landmarks"] = {"model_start": START, "stub_endpoint": TIP}
    assert post_job(client, record, ack=ack).status_code == 422
    record["landmarks"] = "invalid"
    assert post_job(client, record, ack=ack).status_code == 400


def test_strict_mode_cannot_be_overridden(tmp_path, stub_glb):
    client = TestClient(create_app(PASSCODE, tmp_path / "strict", cookie_secure=False,
                                   quality_mode="strict", start_worker=False))
    login(client)
    ack = quality_token(client)
    response = post_job(client, capture(targets_for(stub_glb)), ack=ack)
    assert response.status_code == 422
    assert response.json()["photo_quality"]["status"] == "block"
    assert not list((tmp_path / "strict" / "jobs").iterdir())


def test_strict_pass_uses_existing_preparation_mask(tmp_path, stub_glb):
    image, mask = scene()
    calls = []

    def segment(image):
        calls.append(1)
        return mask

    client = TestClient(create_app(PASSCODE, tmp_path / "pass", cookie_secure=False,
                                   quality_mode="strict", start_worker=False, prep_mask=segment))
    login(client)
    data = png_bytes(image)
    landmarks = {"start": START, "tip": TIP}
    response = client.post("/api/preparations", data={"landmarks": json.dumps(landmarks)},
                           files={"photo": ("main.png", data, "image/png")})
    assert response.status_code == 201, response.text
    prep = response.json()["id"]
    record = capture(targets_for(stub_glb))
    record["landmarks"] = {"model_start": START, "stub_endpoint": TIP}
    ack = quality_token(client, data, landmarks, prep)
    response = post_job(client, record, photo=data, preparation=prep)
    assert response.status_code == 201, response.text
    assert response.json()["photo_quality"]["status"] == "pass"
    assert ack and len(calls) == 1  # no second GPU segmentation for either quality check


def test_invalid_quality_mode_fails_at_startup(tmp_path):
    with pytest.raises(ValueError, match="EPICS_QUALITY_MODE"):
        create_app(PASSCODE, tmp_path, quality_mode="off")
