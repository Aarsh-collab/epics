"""Passcode-protected web service: capture upload -> pipeline -> downloads.

Runs on the DGX Spark inside the TRELLIS.2 image (``deploy/spark/up.sh``) and
serves the built capture app from the same origin. One worker thread runs jobs
in submission order, so a single TRELLIS.2 generation uses the GPU at a time and
the loaded model is reused between jobs. Jobs are not kept: the app discards a
job (uploaded photos and results) when the user leaves its page, and anything
left behind, e.g. by a closed laptop, is deleted after ``EPICS_RETENTION_HOURS``.
Photo preparations (``/api/preparations``: background removed, cut at Start,
end face) follow the same rule; submitting a job moves the approved one into
the job folder, so it is deleted with the job.

Environment:
    EPICS_PASSCODE        required; shared passcode for the whole site
    EPICS_DATA_DIR        job folders (default ./data)
    EPICS_WEB_DIST        built web app to serve at / (optional)
    EPICS_RETENTION_HOURS delete finished jobs nobody discarded after this (default 1)
    EPICS_MAX_QUEUE       reject new jobs while this many are waiting (default 5)
    EPICS_COOKIE_SECURE   "0" to allow the session cookie over plain http (tests)

A finished job is reference geometry pending human review, exactly as
``epics-pipeline`` reports it; the service adds no accuracy claim of its own.
"""

from __future__ import annotations

import hashlib
import hmac
import io
import json
import os
import queue
import re
import secrets
import shutil
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image

from . import pipeline
from .measurements import MeasurementError, parse_measurements

COOKIE = "epics_session"
MAX_PHOTO_BYTES = 15 * 1024 * 1024
MAX_CAPTURE_BYTES = 1024 * 1024
MAX_BODY_BYTES = 2 * MAX_PHOTO_BYTES + MAX_CAPTURE_BYTES + 64 * 1024
PHOTO_FORMATS = {"JPEG", "PNG", "WEBP"}
SAFE_NAME = re.compile(r"^[\w][\w .()+-]{0,127}$")
# Files a client may download from a job's output folder.
RESULT_FILES = ("scaled.glb", "generated.glb", "outer_shell.glb", "outer_shell_review.glb",
                "manifest.json", "scale_report.json", "validation.json",
                "measurements.json", "outer_shell_report.json", "prepared.png", "model_input.png")
# Files a client may fetch from a preparation: previews on neutral grey, with and without the face outlined.
PREP_FILES = ("preview.png", "preview_face.png", "prepared.png", "mask.png")
PREP_CAPS = ("straight", "oval")
MEDIA_TYPES = {".glb": "model/gltf-binary", ".png": "image/png", ".json": "application/json"}
LOGIN_WINDOW_S = 15 * 60
LOGIN_FAILS_PER_CLIENT = 10
LOGIN_FAILS_TOTAL = 50


def _cleanup_summary(out: Path) -> dict | None:
    """The per-piece decisions of the outer-wall cleanup, for the result page."""
    path = out / "outer_shell_report.json"
    if not path.is_file():
        return None
    report = json.loads(path.read_text())
    repairs = report.get("repairs") or {}
    outer = repairs.get("outer_wall") or {}
    return {
        "status": report.get("status"), "reason": report.get("reason"),
        "pieces": [{key: p.get(key) for key in ("piece", "faces", "decision", "review_flag")}
                   for p in report.get("pieces", [])],
        "holes_filled": sum(1 for h in outer.get("holes", []) if h.get("filled")),
        "faces_removed": outer.get("back_to_back_faces_removed", 0) + outer.get("repeated_faces_removed", 0)
        + outer.get("malformed_faces_removed", 0),
        "welded": repairs.get("welded_pieces", []),
        "fused": [f["piece"] for f in repairs.get("fused", [])],
        "texture": report.get("texture"),
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Jobs:
    """Job folders on disk plus a single in-order worker thread."""

    def __init__(self, data_dir: Path, *, max_queue: int, retention_hours: float, run=None):
        self.root = Path(data_dir) / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_queue = max_queue
        self.retention_s = retention_hours * 3600
        self.run = run or pipeline.run
        self.lock = threading.Lock()
        self.waiting: list[str] = []
        self.queue: queue.Queue[str] = queue.Queue()
        self._recover()

    def _recover(self) -> None:
        # Jobs that were queued or running when the service stopped will never finish.
        for path in self.root.glob("*/job.json"):
            job = json.loads(path.read_text())
            if job["status"] in ("queued", "running"):
                job.update(status="interrupted", finished=_now())
                path.write_text(json.dumps(job, indent=2) + "\n")

    def start(self) -> None:
        threading.Thread(target=self._work, name="epics-worker", daemon=True).start()

    def dir(self, job_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", job_id):
            raise HTTPException(404, "no such job")
        path = self.root / job_id
        if not (path / "job.json").is_file():
            raise HTTPException(404, "no such job")
        return path

    def read(self, job_id: str) -> dict:
        return json.loads((self.dir(job_id) / "job.json").read_text())

    def _write(self, job: dict) -> None:
        (self.root / job["id"] / "job.json").write_text(json.dumps(job, indent=2) + "\n")

    def submit(self, capture: dict, photos: dict[str, bytes], options: dict, prep: Path | None = None) -> dict:
        with self.lock:
            if len(self.waiting) >= self.max_queue:
                raise HTTPException(503, "The generation queue is full. Try again in a few minutes.")
            job_id = secrets.token_urlsafe(18)
            input_dir = self.root / job_id / "input"
            input_dir.mkdir(parents=True)
            (input_dir / pipeline.CAPTURE_FILE).write_text(json.dumps(capture, indent=2) + "\n")
            for name, data in photos.items():
                (input_dir / name).write_bytes(data)
            if prep is not None:
                shutil.move(str(prep), input_dir / pipeline.PREP_DIR)
            job = {"id": job_id, "status": "queued", "created": _now(), "started": None,
                   "finished": None, "options": options}
            self._write(job)
            self.waiting.append(job_id)
            self.queue.put(job_id)
        return job

    def position(self, job_id: str) -> int | None:
        with self.lock:
            return self.waiting.index(job_id) + 1 if job_id in self.waiting else None

    def _work(self) -> None:
        while True:
            try:
                job_id = self.queue.get(timeout=300)
            except queue.Empty:
                self.cleanup()
                continue
            with self.lock:
                if job_id not in self.waiting:  # discarded while queued
                    continue
                self.waiting.remove(job_id)
                job = self.read(job_id)
                job.update(status="running", started=_now())
                self._write(job)
            from .trellis_run import TrellisSettings

            opts = job["options"]
            try:
                self.run(self.root / job_id / "out", capture_dir=self.root / job_id / "input",
                         settings=TrellisSettings(remesh=opts["remesh"]), outer_shell=opts["outer_shell"])
            except Exception as exc:  # pipeline.run records its own errors; keep the worker alive regardless
                job["error"] = f"{type(exc).__name__}: {exc}"
            with self.lock:
                if self.read(job_id).get("discard"):
                    shutil.rmtree(self.root / job_id, ignore_errors=True)
                else:
                    job.update(status="done", finished=_now())
                    self._write(job)
            self.cleanup()

    def discard(self, job_id: str) -> None:
        """Delete a job's uploads and results now, or as soon as its run finishes."""
        with self.lock:
            path = self.dir(job_id)
            job = self.read(job_id)
            if job["status"] == "running":
                job["discard"] = True
                self._write(job)
                return
            if job_id in self.waiting:
                self.waiting.remove(job_id)
            shutil.rmtree(path, ignore_errors=True)

    def cleanup(self) -> None:
        cutoff = time.time() - self.retention_s
        for path in (self.root.parent / "preps").glob("*/prep.json"):
            if path.stat().st_mtime < cutoff:
                shutil.rmtree(path.parent, ignore_errors=True)
        for path in self.root.glob("*/job.json"):
            job = json.loads(path.read_text())
            ended = datetime.fromisoformat(job["finished"] or job["created"]).timestamp()
            if job["status"] not in ("queued", "running") and ended < cutoff:
                shutil.rmtree(path.parent, ignore_errors=True)


class Preparations:
    """Prepared generation photos awaiting approval, one folder each under ``data/preps``."""

    def __init__(self, data_dir: Path, mask_fn=None):
        self.root = Path(data_dir) / "preps"
        self.root.mkdir(parents=True, exist_ok=True)
        self.mask_fn = mask_fn
        self.lock = threading.Lock()  # one background-removal model, one preparation at a time

    def dir(self, prep_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{16,64}", prep_id or ""):
            raise HTTPException(404, "no such preparation")
        path = self.root / prep_id
        if not (path / "prep.json").is_file():
            raise HTTPException(404, "no such preparation")
        return path

    def create(self, filename: str, data: bytes, start: dict, tip: dict, cap: str) -> dict:
        from . import photo_prep

        mask_fn = self.mask_fn or photo_prep.trellis_mask
        with self.lock:
            try:
                prep = photo_prep.prepare(data, start, tip, mask_fn, photo_prep.PrepSettings(cap=cap))
            except photo_prep.PrepError as exc:
                raise HTTPException(422, str(exc))
        prep_id = secrets.token_urlsafe(18)
        path = self.root / prep_id
        path.mkdir(parents=True)
        prepared = photo_prep.png_bytes(prep.rgba)
        files = {"prepared.png": prepared, "mask.png": photo_prep.png_bytes(prep.mask),
                 "face.png": photo_prep.png_bytes(prep.face),
                 "preview.png": photo_prep.png_bytes(photo_prep.preview(prep)),
                 "preview_face.png": photo_prep.png_bytes(photo_prep.preview(prep, outline=True)),
                 "original" + Path(filename).suffix.lower(): data}
        for name, blob in files.items():
            (path / name).write_bytes(blob)
        record = {**prep.record, "id": prep_id, "created": _now(), "photo_filename": filename,
                  "sha256": {name: photo_prep.sha256(blob) for name, blob in files.items()},
                  "original_sha256": photo_prep.sha256(data), "prepared_sha256": photo_prep.sha256(prepared)}
        (path / "prep.json").write_text(json.dumps(record, indent=2) + "\n")
        return record

    def discard(self, prep_id: str) -> None:
        shutil.rmtree(self.dir(prep_id), ignore_errors=True)


def _prep_view(record: dict) -> dict:
    return {"id": record["id"], "cap": record["settings"]["cap"], "warnings": record["warnings"],
            "landmarks": record["landmarks_normalized"], "photo_filename": record["photo_filename"],
            "face": record["face"], "files": list(PREP_FILES)}


def _check_photo(name: str, data: bytes) -> None:
    if len(data) > MAX_PHOTO_BYTES:
        raise HTTPException(413, f"{name} is larger than 15 MB")
    try:
        with Image.open(io.BytesIO(data)) as img:
            fmt = img.format
            img.verify()
    except Exception:
        raise HTTPException(400, f"{name} is not a readable image")
    if fmt not in PHOTO_FORMATS:
        raise HTTPException(400, f"{name} must be JPG, PNG or WebP")


def _check_capture(capture: dict) -> list[str]:
    """Validate a capture record and return the photo filenames it names."""
    if not isinstance(capture, dict) or capture.get("schema_version") != "capture.v1":
        raise HTTPException(400, "capture must be a capture.v1 record")
    photos = capture.get("photos")
    if not isinstance(photos, list) or not 1 <= len(photos) <= 2:
        raise HTTPException(400, "capture must name one or two photos")
    names = [p.get("filename") if isinstance(p, dict) else None for p in photos]
    if any(not isinstance(n, str) or not SAFE_NAME.match(n) or n == pipeline.CAPTURE_FILE for n in names):
        raise HTTPException(400, "photo filenames may only use letters, digits, spaces and . _ - ( ) +")
    if len(set(names)) != len(names):
        raise HTTPException(400, "the two photos need different filenames")
    if not any(p.get("role") == "generation" for p in photos):
        raise HTTPException(400, "capture has no photo with role 'generation'")
    measurements = capture.get("stub_measurements")
    if measurements is None:
        raise HTTPException(400, "This capture has incomplete measurements, so it cannot be scaled.")
    try:
        parse_measurements(measurements)
    except MeasurementError as exc:
        raise HTTPException(400, f"measurements: {exc}")
    return names


def _job_view(jobs: Jobs, job: dict) -> dict:
    view = {k: job.get(k) for k in ("id", "status", "created", "started", "finished", "options", "error")}
    view["position"] = jobs.position(job["id"]) if job["status"] == "queued" else None
    out = jobs.root / job["id"] / "out"
    manifest_path = out / "manifest.json"
    if job["status"] == "done" and manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        view["result"] = {
            "status": manifest["status"],
            "failed_stage": manifest["failed_stage"],
            "error": (manifest.get("error") or {}).get("message"),
            "hint": manifest.get("hint"),
            "review_status": manifest["review_status"],
            "elapsed_s": manifest.get("elapsed_s"),
            "scale": manifest["stages"].get("scale"),
            "cleanup": _cleanup_summary(out),
            "validation": (manifest["stages"].get("validate") or {}).get("summary"),
            "limitations": manifest["limitations"],
        }
    view["files"] = [name for name in RESULT_FILES if (out / name).is_file()]
    return view


def create_app(passcode: str, data_dir: Path, *, web_dist: Path | None = None, max_queue: int = 5,
               retention_hours: float = 1, cookie_secure: bool = True, run=None, start_worker: bool = True,
               prep_mask=None) -> FastAPI:
    if not passcode:
        raise ValueError("EPICS_PASSCODE must be set")
    token = hmac.new(passcode.encode(), b"epics-session-v1", hashlib.sha256).hexdigest()
    jobs = Jobs(data_dir, max_queue=max_queue, retention_hours=retention_hours, run=run)
    if start_worker:
        jobs.start()
    fails: dict[str, deque] = {}
    all_fails: deque = deque()
    app = FastAPI(title="EPICS reference mesh service", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.jobs = jobs
    preps = Preparations(data_dir, mask_fn=prep_mask)

    def authed(request: Request) -> bool:
        return hmac.compare_digest(request.cookies.get(COOKIE, ""), token)

    def require(request: Request) -> None:
        if not authed(request):
            raise HTTPException(401, "Enter the passcode first.")

    def client(request: Request) -> str:
        forwarded = request.headers.get("x-forwarded-for", "")
        return forwarded.split(",")[0].strip() or (request.client.host if request.client else "?")

    @app.get("/api/session")
    def session(request: Request):
        return {"authenticated": authed(request)}

    @app.post("/api/login")
    async def login(request: Request):
        now = time.monotonic()
        mine = fails.setdefault(client(request), deque())
        for q in (mine, all_fails):
            while q and now - q[0] > LOGIN_WINDOW_S:
                q.popleft()
        if len(mine) >= LOGIN_FAILS_PER_CLIENT or len(all_fails) >= LOGIN_FAILS_TOTAL:
            raise HTTPException(429, "Too many wrong passcodes. Wait 15 minutes and try again.")
        try:
            given = str((await request.json()).get("passcode", ""))
        except Exception:
            raise HTTPException(400, "send {\"passcode\": ...}")
        if not hmac.compare_digest(given.encode(), passcode.encode()):
            mine.append(now)
            all_fails.append(now)
            raise HTTPException(401, "That passcode is not right.")
        response = JSONResponse({"authenticated": True})
        response.set_cookie(COOKIE, token, max_age=7 * 86400, httponly=True, secure=cookie_secure, samesite="strict")
        return response

    @app.post("/api/logout")
    def logout():
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE)
        return response

    @app.post("/api/jobs")
    async def submit(request: Request):
        require(request)  # before the body is read, so anonymous uploads never touch disk
        length = request.headers.get("content-length")
        if length is None or not length.isdigit():
            raise HTTPException(411, "Content-Length required")
        if int(length) > MAX_BODY_BYTES:
            raise HTTPException(413, "Upload is larger than two 15 MB photos")
        form = await request.form(max_files=2, max_fields=6, max_part_size=MAX_CAPTURE_BYTES)
        try:
            capture = json.loads(form.get("capture") or "")
        except (TypeError, json.JSONDecodeError):
            raise HTTPException(400, "capture field must be the capture.v1 JSON")
        names = _check_capture(capture)
        photos: dict[str, bytes] = {}
        for upload in form.getlist("photos"):
            if isinstance(upload, str) or upload.filename not in names:
                raise HTTPException(400, "each uploaded photo must match a filename in the capture")
            data = await upload.read()
            _check_photo(upload.filename, data)
            photos[upload.filename] = data
        if set(photos) != set(names):
            raise HTTPException(400, f"upload the photos named in the capture: {', '.join(names)}")
        options = {"outer_shell": form.get("outer_shell") == "true", "remesh": form.get("remesh") != "false"}
        prep_dir = None
        if form.get("preparation"):
            # The approved preview must be of this photo and these landmarks; otherwise it is out of date.
            prep_dir = preps.dir(str(form.get("preparation")))
            record = json.loads((prep_dir / "prep.json").read_text())
            main = next(p["filename"] for p in capture["photos"] if p.get("role") == "generation")
            landmarks = capture.get("landmarks") or {}
            current = {"start": landmarks.get("model_start"), "tip": landmarks.get("stub_endpoint")}
            if (hashlib.sha256(photos[main]).hexdigest() != record["original_sha256"]
                    or current != record["landmarks_normalized"]):
                raise HTTPException(409, "The prepared photo is out of date: the photo or the Start/Tip markers "
                                         "changed. Prepare and approve the photo again.")
            capture["preparation"] = {"id": record["id"], "cap": record["settings"]["cap"],
                                      "prepared_sha256": record["prepared_sha256"],
                                      "original_sha256": record["original_sha256"]}
            options["preparation"] = record["settings"]["cap"]
        job = jobs.submit(capture, photos, options, prep_dir)
        return JSONResponse(_job_view(jobs, job), status_code=201)

    @app.post("/api/preparations")
    async def prepare(request: Request):
        require(request)
        length = request.headers.get("content-length")
        if length is None or not length.isdigit():
            raise HTTPException(411, "Content-Length required")
        if int(length) > MAX_PHOTO_BYTES + 64 * 1024:
            raise HTTPException(413, "Upload is larger than one 15 MB photo")
        form = await request.form(max_files=1, max_fields=3, max_part_size=64 * 1024)
        upload = form.get("photo")
        if upload is None or isinstance(upload, str) or not SAFE_NAME.match(upload.filename or ""):
            raise HTTPException(400, "send the main photo as 'photo'")
        try:
            landmarks = json.loads(form.get("landmarks") or "")
            start, tip = landmarks["start"], landmarks["tip"]
            start, tip = ({"x": float(p["x"]), "y": float(p["y"])} for p in (start, tip))
        except (TypeError, KeyError, ValueError, json.JSONDecodeError):
            raise HTTPException(400, "landmarks must be {\"start\": {x, y}, \"tip\": {x, y}} in 0-1 photo coordinates")
        cap = form.get("cap") or "straight"
        if cap not in PREP_CAPS:
            raise HTTPException(400, f"cap must be one of {', '.join(PREP_CAPS)}")
        data = await upload.read()
        _check_photo(upload.filename, data)
        from starlette.concurrency import run_in_threadpool

        record = await run_in_threadpool(preps.create, upload.filename, data, start, tip, cap)
        return JSONResponse(_prep_view(record), status_code=201)

    @app.get("/api/preparations/{prep_id}/files/{name}")
    def prep_file(prep_id: str, name: str, request: Request):
        require(request)
        path = preps.dir(prep_id) / name
        if name not in PREP_FILES or not path.is_file():
            raise HTTPException(404, "no such file")
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/api/preparations/{prep_id}/discard", status_code=204)
    def prep_discard(prep_id: str, request: Request):
        require(request)
        preps.discard(prep_id)

    @app.get("/api/jobs/{job_id}")
    def status(job_id: str, request: Request):
        require(request)
        return _job_view(jobs, jobs.read(job_id))

    @app.post("/api/jobs/{job_id}/discard", status_code=204)
    def discard(job_id: str, request: Request):
        require(request)  # also sent by navigator.sendBeacon when the page is left
        jobs.discard(job_id)

    @app.get("/api/jobs/{job_id}/files/{name}")
    def download(job_id: str, name: str, request: Request):
        require(request)
        path = jobs.dir(job_id) / "out" / name
        if name not in RESULT_FILES or not path.is_file():
            raise HTTPException(404, "no such file")
        return FileResponse(path, media_type=MEDIA_TYPES[path.suffix], filename=name)

    if web_dist is not None:
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
    return app


def main() -> None:
    import uvicorn

    web_dist = os.environ.get("EPICS_WEB_DIST")
    app = create_app(
        os.environ.get("EPICS_PASSCODE", ""),
        Path(os.environ.get("EPICS_DATA_DIR", "data")),
        web_dist=Path(web_dist) if web_dist else None,
        max_queue=int(os.environ.get("EPICS_MAX_QUEUE", "5")),
        retention_hours=float(os.environ.get("EPICS_RETENTION_HOURS", "1")),
        cookie_secure=os.environ.get("EPICS_COOKIE_SECURE", "1") != "0",
    )
    uvicorn.run(app, host=os.environ.get("EPICS_HOST", "0.0.0.0"), port=int(os.environ.get("EPICS_PORT", "8000")),
                proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
