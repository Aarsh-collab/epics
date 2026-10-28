import io
import json
import threading
import time

import pytest
from PIL import Image

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient

from epics_geometry import pipeline
from epics_geometry.server import create_app
from test_pipeline import metres, targets_for
from epics_geometry.synthetic import stub_meshes, stub_scene

PASSCODE = "test-passcode"


def png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def stub_glb(tmp_path):
    path = tmp_path / "generated.glb"
    stub_scene(metres(stub_meshes())).export(path)
    return path


@pytest.fixture
def client(tmp_path, stub_glb):
    def fake_run(out_dir, *, capture_dir, settings, outer_shell):
        # Real scaling and validation, with the synthetic GLB standing in for TRELLIS.2.
        return pipeline.run(out_dir, capture_dir=capture_dir, glb=stub_glb, outer_shell=outer_shell)

    app = create_app(PASSCODE, tmp_path / "data", cookie_secure=False, run=fake_run)
    return TestClient(app)


def capture(measurements):
    return {
        "schema_version": "capture.v1",
        "photos": [{"role": "generation", "filename": "main.png"}, {"role": "review", "filename": "side.png"}],
        "stub_measurements": measurements,
    }


def submit(client, record, photos=("main.png", "side.png")):
    return client.post("/api/jobs", data={"capture": json.dumps(record)},
                       files=[("photos", (name, png(), "image/png")) for name in photos])


def login(client):
    assert client.post("/api/login", json={"passcode": PASSCODE}).status_code == 200


def wait(client, job_id):
    for _ in range(200):
        job = client.get(f"/api/jobs/{job_id}").json()
        if job["status"] == "done":
            return job
        time.sleep(0.05)
    raise AssertionError("job did not finish")


def test_api_needs_passcode(client, stub_glb):
    assert client.get("/api/session").json() == {"authenticated": False}
    assert submit(client, capture(targets_for(stub_glb))).status_code == 401
    assert client.get("/api/jobs/" + "a" * 24).status_code == 401
    assert client.post("/api/login", json={"passcode": "wrong"}).status_code == 401
    login(client)
    assert client.get("/api/session").json() == {"authenticated": True}


def test_wrong_passcodes_are_rate_limited(client):
    for _ in range(10):
        assert client.post("/api/login", json={"passcode": "wrong"}).status_code == 401
    assert client.post("/api/login", json={"passcode": PASSCODE}).status_code == 429


def test_capture_to_scaled_download(client, stub_glb):
    login(client)
    response = submit(client, capture(targets_for(stub_glb)))
    assert response.status_code == 201, response.text
    job = wait(client, response.json()["id"])
    assert job["result"]["status"] == "complete", job
    assert job["result"]["review_status"] == "pending_human_review"
    assert "scaled.glb" in job["files"]
    glb = client.get(f"/api/jobs/{job['id']}/files/scaled.glb")
    assert glb.status_code == 200 and glb.content[:4] == b"glTF"
    assert client.get(f"/api/jobs/{job['id']}/files/job.json").status_code == 404


def test_rejects_bad_uploads(client, stub_glb):
    login(client)
    good = capture(targets_for(stub_glb))
    assert submit(client, good, photos=("main.png",)).status_code == 400
    assert submit(client, good, photos=("main.png", "other.png")).status_code == 400
    assert submit(client, {**good, "stub_measurements": None}).status_code == 400
    traversal = {**good, "photos": [{"role": "generation", "filename": "../main.png"}]}
    assert submit(client, traversal, photos=("../main.png",)).status_code == 400
    not_image = client.post("/api/jobs", data={"capture": json.dumps(good)},
                            files=[("photos", (n, b"not an image", "image/png")) for n in ("main.png", "side.png")])
    assert not_image.status_code == 400


def test_unscalable_mesh_reports_failure(client, stub_glb):
    login(client)
    m = targets_for(stub_glb)
    m["sections"][1]["circumference_mm"] *= 3  # cannot match the stub's shape
    m["sections"][1]["max_width_mm"] *= 3
    job = wait(client, submit(client, capture(m)).json()["id"])
    assert job["result"]["status"] != "complete"
    assert "scaled.glb" not in job["files"]


def test_discard_deletes_finished_job(client, stub_glb, tmp_path):
    login(client)
    job = wait(client, submit(client, capture(targets_for(stub_glb))).json()["id"])
    assert client.post(f"/api/jobs/{job['id']}/discard").status_code == 204
    assert not (tmp_path / "data" / "jobs" / job["id"]).exists()
    assert client.get(f"/api/jobs/{job['id']}").status_code == 404


def test_discard_while_running_deletes_after_run(tmp_path, stub_glb):
    started, release = threading.Event(), threading.Event()

    def slow_run(out_dir, *, capture_dir, settings, outer_shell):
        started.set()
        release.wait(10)
        return pipeline.run(out_dir, capture_dir=capture_dir, glb=stub_glb)

    client = TestClient(create_app(PASSCODE, tmp_path / "data", cookie_secure=False, run=slow_run))
    login(client)
    job_id = submit(client, capture(targets_for(stub_glb))).json()["id"]
    assert started.wait(10)
    assert client.post(f"/api/jobs/{job_id}/discard").status_code == 204
    assert client.get(f"/api/jobs/{job_id}").json()["status"] == "running"
    release.set()
    for _ in range(200):
        if client.get(f"/api/jobs/{job_id}").status_code == 404:
            break
        time.sleep(0.05)
    assert not (tmp_path / "data" / "jobs" / job_id).exists()




def test_cleanup_report_reaches_the_result(client, stub_glb):
    login(client)
    response = client.post("/api/jobs", data={"capture": json.dumps(capture(targets_for(stub_glb))),
                                              "outer_shell": "true"},
                           files=[("photos", (n, png(), "image/png")) for n in ("main.png", "side.png")])
    job = wait(client, response.json()["id"])
    # The synthetic stub has two mesh instances, which the cleanup refuses.
    assert job["result"]["status"] == "cleanup_refused"
    assert job["result"]["cleanup"]["status"] == "refused"
    assert "outer_shell_report.json" in job["files"] and "generated.glb" not in job["files"]


def limb_png() -> bytes:
    from test_photo_prep import photo

    img, _ = photo((400, 240), (-20, 120), (330, 120), 40)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


LANDMARKS = {"start": {"x": 0.25, "y": 0.5}, "tip": {"x": 0.925, "y": 0.5}}


@pytest.fixture
def prep_client(tmp_path, stub_glb):
    from test_photo_prep import mask_fn

    seen = {}

    def fake_run(out_dir, *, capture_dir, settings, outer_shell):
        seen["capture_dir"] = capture_dir
        return pipeline.run(out_dir, capture_dir=capture_dir, glb=stub_glb, outer_shell=outer_shell)

    app = create_app(PASSCODE, tmp_path / "data", cookie_secure=False, run=fake_run, prep_mask=mask_fn)
    client = TestClient(app)
    client.seen = seen
    return client


def prepare(client, photo=None, landmarks=LANDMARKS, cap="oval"):
    return client.post("/api/preparations", data={"landmarks": json.dumps(landmarks), "cap": cap},
                       files={"photo": ("main.png", photo or limb_png(), "image/png")})


def prepared_capture(stub_glb, landmarks=LANDMARKS):
    record = capture(targets_for(stub_glb))
    record["landmarks"] = {"coordinate_system": "normalized_image_xy_top_left",
                           "model_start": landmarks["start"], "stub_endpoint": landmarks["tip"]}
    return record


def submit_prepared(client, record, prep_id, photo=None):
    return client.post("/api/jobs", data={"capture": json.dumps(record), "preparation": prep_id},
                       files=[("photos", ("main.png", photo or limb_png(), "image/png")),
                              ("photos", ("side.png", png(), "image/png"))])


def test_preparation_needs_passcode(prep_client):
    assert prepare(prep_client).status_code == 401
    assert prep_client.get("/api/preparations/" + "a" * 24 + "/files/preview.png").status_code == 401


def test_preparation_preview_and_job_use_the_approved_image(prep_client, stub_glb, tmp_path):
    login(prep_client)
    response = prepare(prep_client)
    assert response.status_code == 201, response.text
    prep = response.json()
    assert prep["cap"] == "oval" and prep["warnings"] == []
    for name in ("preview.png", "preview_face.png"):
        r = prep_client.get(f"/api/preparations/{prep['id']}/files/{name}")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert prep_client.get(f"/api/preparations/{prep['id']}/files/prep.json").status_code == 404
    stored = tmp_path / "data" / "preps" / prep["id"]
    assert {"original.png", "mask.png", "prepared.png", "prep.json"} <= {p.name for p in stored.iterdir()}

    response = submit_prepared(prep_client, prepared_capture(stub_glb), prep["id"])
    assert response.status_code == 201, response.text
    job = wait(prep_client, response.json()["id"])
    assert not stored.exists()  # moved into the job, so it is deleted with it
    capture_dir = prep_client.seen["capture_dir"]
    saved = json.loads((capture_dir / "capture.json").read_text())
    assert saved["preparation"]["id"] == prep["id"]
    assert (capture_dir / "prep" / "prepared.png").is_file()
    assert "prepared.png" in job["files"]
    prep_client.post(f"/api/jobs/{job['id']}/discard")
    assert not capture_dir.parent.exists()


def test_changed_markers_or_photo_invalidate_the_preparation(prep_client, stub_glb):
    login(prep_client)
    prep_id = prepare(prep_client).json()["id"]
    moved = {"start": {"x": 0.3, "y": 0.5}, "tip": LANDMARKS["tip"]}
    assert submit_prepared(prep_client, prepared_capture(stub_glb, moved), prep_id).status_code == 409
    other = io.BytesIO()
    Image.open(io.BytesIO(limb_png())).rotate(1).save(other, format="PNG")
    assert submit_prepared(prep_client, prepared_capture(stub_glb), prep_id, other.getvalue()).status_code == 409
    assert submit_prepared(prep_client, prepared_capture(stub_glb), "b" * 24).status_code == 404


def test_preparation_failures_are_explained(prep_client):
    login(prep_client)
    off_limb = {"start": {"x": 0.25, "y": 0.05}, "tip": {"x": 0.925, "y": 0.5}}
    response = prepare(prep_client, landmarks=off_limb)
    assert response.status_code == 422 and "Start" in response.json()["detail"]
    same = {"start": {"x": 0.5, "y": 0.5}, "tip": {"x": 0.505, "y": 0.5}}
    assert "too close" in prepare(prep_client, landmarks=same).json()["detail"]
    assert prepare(prep_client, cap="wavy").status_code == 400


def test_preparation_discard_and_retention(prep_client, tmp_path):
    login(prep_client)
    prep_id = prepare(prep_client).json()["id"]
    assert prep_client.post(f"/api/preparations/{prep_id}/discard").status_code == 204
    assert not (tmp_path / "data" / "preps" / prep_id).exists()
    prep_id = prepare(prep_client).json()["id"]
    path = tmp_path / "data" / "preps" / prep_id / "prep.json"
    import os

    os.utime(path, (0, 0))
    prep_client.app.state.jobs.cleanup()
    assert not path.parent.exists()
