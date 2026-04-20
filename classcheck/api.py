"""ClassCheck HTTP API — FastAPI wrapper over services.py.

    uvicorn classcheck.api:app --reload --port 8000

OpenAPI UI at http://localhost:8000/docs.

This module owns nothing — every route translates an HTTP request into a
call on the existing `classcheck.services` / `classcheck.reports` /
`classcheck.emailer` functions and serialises the result. Business
logic lives in those modules, unchanged.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import date, time
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

from classcheck import emailer as emailer_mod
from classcheck import reports as reports_mod
from classcheck import services
from classcheck.models import Role, get_session, init_db
from classcheck.paths import db_url as _default_db_url
from classcheck.scripts.enroll import enroll_person

logger = logging.getLogger("classcheck.api")

_pipeline = None  # lazy — first /enroll or /capture request loads it


def _db_url() -> str:
    """Resolve the DB URL fresh on every call.

    Not cached so that ``CLASSCHECK_HOME`` / ``CLASSCHECK_DB`` changes at
    runtime (tests, hot-reload) take effect immediately.
    """
    return _default_db_url()


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db(_db_url())
    s = get_session(_db_url())
    try:
        services.default_room_id(s)
    finally:
        s.close()
    yield
    global _pipeline
    if _pipeline is not None:
        try:
            _pipeline.close()
        except Exception:  # noqa: BLE001
            pass
        _pipeline = None


app = FastAPI(
    title="ClassCheck API",
    version="0.1.0",
    description="Attendance via scheduled face-recognition bursts.",
    lifespan=lifespan,
)

# Next.js dev server runs on :3000. Production is same-origin so this
# only matters in local dev; keeping it permissive for the dev loop.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def _get_pipeline():
    """Lazy-load the face recognition pipeline.

    Takes ~15 s the first time (ONNX weights + FAISS index). Reused
    across all enroll and capture requests for the lifetime of the
    process.
    """
    global _pipeline
    if _pipeline is None:
        from facestack.pipeline import FaceStackPipeline

        from classcheck.paths import build_facestack_config

        logger.info("Loading face recognition pipeline (first request)...")
        _pipeline = FaceStackPipeline(config=build_facestack_config())
    return _pipeline


# ---------------------------------------------------------------------------
# Pydantic response / request shapes
# ---------------------------------------------------------------------------


class ClassOut(BaseModel):
    id: int
    label: str
    time_of_day: str  # "HH:MM"
    teacher_id: Optional[int]
    teacher_name: Optional[str]
    student_count: int


class ClassDetailOut(BaseModel):
    id: int
    label: str
    time_of_day: str
    teacher_id: Optional[int]
    student_ids: list[int]


class ClassCreate(BaseModel):
    label: str
    time_of_day: str  # "HH:MM"
    teacher_id: Optional[int] = None
    student_ids: list[int] = []


class ClassUpdate(BaseModel):
    """All fields optional. An omitted field means 'leave alone'; an
    explicit null for `teacher_id` means 'clear the teacher'."""

    label: Optional[str] = None
    time_of_day: Optional[str] = None
    teacher_id: Optional[int] = None
    student_ids: Optional[list[int]] = None


class PersonOut(BaseModel):
    id: int
    name: str
    role: str
    email: Optional[str]
    class_count: int


class PersonDetailOut(BaseModel):
    id: int
    name: str
    role: str
    email: Optional[str]
    teaches_ids: list[int]
    enrolled_ids: list[int]
    recent_observations: list[dict]


class PersonUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    email: Optional[str] = None


class SetClasses(BaseModel):
    class_ids: list[int]


class SnapshotSummaryOut(BaseModel):
    id: int
    schedule_id: int
    class_label: str
    scheduled_date: str  # ISO
    scheduled_time: str  # "HH:MM"
    actual_time: str  # ISO
    present_count: int
    total_count: int
    thumbnail_url: Optional[str]


class SnapshotDetailOut(BaseModel):
    id: int
    class_label: str
    scheduled_date: str
    scheduled_time: str
    actual_time: str
    n_frames: int
    thumbnail_url: Optional[str]
    observations: list[dict]


class EnrollmentResult(BaseModel):
    id: int
    name: str
    role: str
    email: Optional[str]
    facestack_person_id: Optional[int]


class CaptureResult(BaseModel):
    snapshot_id: Optional[int]
    ok: bool


class EmailRequest(BaseModel):
    to: list[str]
    report_type: str  # "roll" | "teachers"
    subject: Optional[str] = None


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _hhmm(t: time) -> str:
    return t.strftime("%H:%M")


def _parse_hhmm(s: str) -> time:
    try:
        hh, mm = s.split(":")
        return time(int(hh), int(mm))
    except (ValueError, AttributeError) as e:
        raise HTTPException(400, f"time_of_day must be 'HH:MM' (got {s!r})") from e


def _class_to_out(c) -> ClassOut:
    return ClassOut(
        id=c.id,
        label=c.label,
        time_of_day=_hhmm(c.time_of_day),
        teacher_id=c.teacher_id,
        teacher_name=c.teacher_name,
        student_count=c.student_count,
    )


def _person_to_out(p) -> PersonOut:
    return PersonOut(
        id=p.id,
        name=p.name,
        role=p.role,
        email=p.email,
        class_count=p.class_count,
    )


def _class_detail_to_out(d: dict) -> ClassDetailOut:
    return ClassDetailOut(
        id=d["id"],
        label=d["label"],
        time_of_day=_hhmm(d["time_of_day"]),
        teacher_id=d["teacher_id"],
        student_ids=list(d["student_ids"]),
    )


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------


@app.get("/classes", response_model=list[ClassOut], tags=["classes"])
def list_classes():
    return [_class_to_out(c) for c in services.list_classes()]


@app.post("/classes", response_model=ClassDetailOut, status_code=201, tags=["classes"])
def create_class(body: ClassCreate):
    try:
        cid = services.create_class(
            label=body.label,
            time_of_day=_parse_hhmm(body.time_of_day),
            teacher_id=body.teacher_id,
            student_ids=body.student_ids or [],
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    detail = services.get_class_detail(cid)
    if detail is None:
        raise HTTPException(500, "class disappeared after creation")
    return _class_detail_to_out(detail)


@app.get("/classes/{class_id}", response_model=ClassDetailOut, tags=["classes"])
def get_class(class_id: int):
    detail = services.get_class_detail(class_id)
    if detail is None:
        raise HTTPException(404, f"class {class_id} not found")
    return _class_detail_to_out(detail)


@app.patch("/classes/{class_id}", response_model=ClassDetailOut, tags=["classes"])
def update_class(class_id: int, body: ClassUpdate):
    # Only forward fields the client actually sent. That way omitting a
    # field means "leave alone" while sending `null` means "clear it"
    # (the latter only matters for teacher_id).
    fields = body.model_fields_set
    changes: dict = {}
    if "label" in fields and body.label is not None:
        changes["label"] = body.label
    if "time_of_day" in fields and body.time_of_day is not None:
        changes["time_of_day"] = _parse_hhmm(body.time_of_day)
    if "teacher_id" in fields:
        changes["teacher_id"] = body.teacher_id
    if "student_ids" in fields and body.student_ids is not None:
        changes["student_ids"] = body.student_ids

    try:
        services.update_class(class_id, **changes)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return get_class(class_id)


@app.delete("/classes/{class_id}", status_code=204, tags=["classes"])
def delete_class(class_id: int):
    services.delete_class(class_id)
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Capture
# ---------------------------------------------------------------------------


@app.post(
    "/classes/{class_id}/capture", response_model=CaptureResult, tags=["capture"]
)
def capture_now(class_id: int, show_preview: bool = Query(True)):
    """Fire a sampling burst immediately for the given class.

    Blocks for ~30 s while the camera captures and the recognizer votes.
    If `show_preview=true`, a cv2 window opens on the host running the
    API — useful on the same machine as the camera. On a remote
    deployment, set `show_preview=false` so nothing tries to create a
    window.
    """
    if services.get_class_detail(class_id) is None:
        raise HTTPException(404, f"class {class_id} not found")
    pipeline = _get_pipeline()
    snap_id = services.capture_now(
        class_id=class_id,
        db_url=_db_url(),
        pipeline=pipeline,
        show_preview=show_preview,
    )
    return CaptureResult(snapshot_id=snap_id, ok=snap_id is not None)


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


@app.get("/people", response_model=list[PersonOut], tags=["people"])
def list_people():
    return [_person_to_out(p) for p in services.list_people()]


def _person_detail_to_out(d) -> PersonDetailOut:
    return PersonDetailOut(
        id=d.id,
        name=d.name,
        role=d.role,
        email=d.email,
        teaches_ids=list(d.teaches_ids),
        enrolled_ids=list(d.enrolled_ids),
        recent_observations=list(d.recent_observations),
    )


@app.get("/people/{person_id}", response_model=PersonDetailOut, tags=["people"])
def get_person(person_id: int):
    d = services.get_person_detail(person_id)
    if d is None:
        raise HTTPException(404, f"person {person_id} not found")
    return _person_detail_to_out(d)


@app.patch("/people/{person_id}", response_model=PersonDetailOut, tags=["people"])
def update_person(person_id: int, body: PersonUpdate):
    fields = body.model_fields_set
    changes: dict = {}
    if "name" in fields and body.name is not None:
        changes["name"] = body.name
    if "role" in fields and body.role is not None:
        changes["role"] = body.role
    if "email" in fields:
        changes["email"] = body.email

    try:
        services.update_person(person_id, **changes)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return get_person(person_id)


@app.delete("/people/{person_id}", status_code=204, tags=["people"])
def delete_person(person_id: int):
    services.delete_person(person_id)
    return Response(status_code=204)


@app.put(
    "/people/{person_id}/classes", response_model=PersonDetailOut, tags=["people"]
)
def set_classes(person_id: int, body: SetClasses):
    try:
        services.set_person_classes(person_id, body.class_ids)
    except LookupError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return get_person(person_id)


# ---------------------------------------------------------------------------
# Enroll
# ---------------------------------------------------------------------------


@app.post("/enroll", response_model=EnrollmentResult, tags=["enroll"])
async def enroll(
    name: str = Form(...),
    role: str = Form(...),
    email: Optional[str] = Form(None),
    photos: list[UploadFile] = File(...),
):
    name = name.strip()
    if not name:
        raise HTTPException(400, "name is required")
    if role not in {r.value for r in Role}:
        raise HTTPException(400, f"unknown role: {role}")
    if not photos:
        raise HTTPException(400, "at least one photo is required")

    frames: list[np.ndarray] = []
    for uf in photos:
        data = await uf.read()
        if not data:
            continue
        arr = np.frombuffer(data, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(400, f"could not decode image: {uf.filename}")
        frames.append(img)

    if not frames:
        raise HTTPException(400, "no readable images in upload")

    pipeline = _get_pipeline()
    try:
        cc_person = enroll_person(
            name=name,
            role=Role(role),
            frames=frames,
            pipeline=pipeline,
            db_url=_db_url(),
            email=(email or None),
        )
    except RuntimeError as e:
        raise HTTPException(400, str(e)) from e

    return EnrollmentResult(
        id=cc_person.id,
        name=cc_person.name,
        role=cc_person.role,
        email=cc_person.email,
        facestack_person_id=cc_person.facestack_person_id,
    )


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------


@app.get("/snapshots", response_model=list[SnapshotSummaryOut], tags=["attendance"])
def list_snapshots(
    on_date: date = Query(..., alias="date"),
    class_id: Optional[int] = None,
):
    snaps = services.list_snapshots_for_date(on_date, class_id=class_id)
    return [
        SnapshotSummaryOut(
            id=s.id,
            schedule_id=s.schedule_id,
            class_label=s.class_label,
            scheduled_date=s.scheduled_date.isoformat(),
            scheduled_time=_hhmm(s.scheduled_time),
            actual_time=s.actual_time.isoformat(),
            present_count=s.present_count,
            total_count=s.total_count,
            thumbnail_url=(
                f"/snapshots/{s.id}/thumbnail" if s.thumbnail_path else None
            ),
        )
        for s in snaps
    ]


@app.get("/snapshots/{snapshot_id}", response_model=SnapshotDetailOut, tags=["attendance"])
def get_snapshot(snapshot_id: int):
    d = services.get_snapshot_detail(snapshot_id)
    if d is None:
        raise HTTPException(404, f"snapshot {snapshot_id} not found")
    return SnapshotDetailOut(
        id=d["id"],
        class_label=d["class_label"],
        scheduled_date=d["scheduled_date"].isoformat(),
        scheduled_time=_hhmm(d["scheduled_time"]),
        actual_time=d["actual_time"].isoformat(),
        n_frames=d["n_frames"],
        thumbnail_url=(
            f"/snapshots/{snapshot_id}/thumbnail" if d["thumbnail_path"] else None
        ),
        observations=list(d["observations"]),
    )


@app.get("/snapshots/{snapshot_id}/thumbnail", tags=["attendance"])
def get_thumbnail(snapshot_id: int):
    d = services.get_snapshot_detail(snapshot_id)
    if d is None or not d["thumbnail_path"]:
        raise HTTPException(404, "no thumbnail for this snapshot")
    path = Path(d["thumbnail_path"])
    if not path.exists():
        raise HTTPException(404, "thumbnail file missing on disk")
    return FileResponse(path, media_type="image/jpeg")


@app.get("/snapshots/{snapshot_id}/roll.csv", tags=["attendance"])
def roll_csv(snapshot_id: int):
    d = services.get_snapshot_detail(snapshot_id)
    if d is None:
        raise HTTPException(404, f"snapshot {snapshot_id} not found")
    session = get_session(_db_url())
    try:
        csv_text = reports_mod.daily_student_roll(
            session, d["class_label"], d["scheduled_date"]
        )
    finally:
        session.close()
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="roll_{d["class_label"]}'
                f'_{d["scheduled_date"]}.csv"'
            )
        },
    )


@app.get("/snapshots/{snapshot_id}/teachers.csv", tags=["attendance"])
def teachers_csv(snapshot_id: int):
    d = services.get_snapshot_detail(snapshot_id)
    if d is None:
        raise HTTPException(404, f"snapshot {snapshot_id} not found")
    session = get_session(_db_url())
    try:
        csv_text = reports_mod.daily_teacher_attendance(session, d["scheduled_date"])
    finally:
        session.close()
    return Response(
        content=csv_text,
        media_type="text/csv",
        headers={
            "Content-Disposition": (
                f'attachment; filename="teachers_{d["scheduled_date"]}.csv"'
            )
        },
    )


@app.post("/snapshots/{snapshot_id}/email", tags=["attendance"])
def email_snapshot(snapshot_id: int, body: EmailRequest):
    d = services.get_snapshot_detail(snapshot_id)
    if d is None:
        raise HTTPException(404, f"snapshot {snapshot_id} not found")
    if not body.to:
        raise HTTPException(400, "`to` must not be empty")
    session = get_session(_db_url())
    try:
        if body.report_type == "roll":
            csv_text = reports_mod.daily_student_roll(
                session, d["class_label"], d["scheduled_date"]
            )
            filename = f'roll_{d["class_label"]}_{d["scheduled_date"]}.csv'
            subject = body.subject or (
                f'Attendance — {d["class_label"]} on {d["scheduled_date"]}'
            )
        elif body.report_type == "teachers":
            csv_text = reports_mod.daily_teacher_attendance(
                session, d["scheduled_date"]
            )
            filename = f'teachers_{d["scheduled_date"]}.csv'
            subject = body.subject or (
                f'Teacher attendance on {d["scheduled_date"]}'
            )
        else:
            raise HTTPException(400, f"unknown report_type: {body.report_type}")
    finally:
        session.close()

    try:
        emailer_mod.send_csv_report(
            to=body.to,
            subject=subject,
            body="Attendance report attached.",
            csv_text=csv_text,
            csv_filename=filename,
        )
    except emailer_mod.EmailConfigError as e:
        raise HTTPException(500, f"SMTP is not configured: {e}") from e
    return {"ok": True, "sent_to": body.to, "filename": filename}


# ---------------------------------------------------------------------------
# Health + CLI entry
# ---------------------------------------------------------------------------


@app.get("/health", tags=["meta"])
def health():
    return {"ok": True, "version": app.version}


def main() -> None:
    """`classcheck-api` console entry point."""
    import uvicorn

    uvicorn.run(
        "classcheck.api:app",
        host="127.0.0.1",
        port=8000,
        reload=False,
    )


if __name__ == "__main__":
    main()
