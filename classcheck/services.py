"""Business-logic services for ClassCheck.

Framework-agnostic — no Streamlit, no Flask, no HTTP. Every mutation or
query the dashboard and CLIs need lives here as a plain Python function.
If we later add a FastAPI layer for a mobile app, it calls the same
functions; nothing about the data or validation changes.

Conventions:
  - Every function takes and closes its own session unless otherwise noted.
  - Reads return plain dataclasses / dicts (detached from SQLAlchemy state)
    so callers can use them freely after the function returns.
  - Writes return the affected row's id (int) or None.
"""

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

from sqlalchemy.orm import joinedload, selectinload

from classcheck.models import (
    Enrollment,
    Observation,
    Person,
    Room,
    Schedule,
    Snapshot,
    get_session,
)


# ---------------------------------------------------------------------------
# Read DTOs — used by the dashboard to render cards without keeping ORM state
# alive.
# ---------------------------------------------------------------------------

@dataclass
class ClassRow:
    id: int
    label: str
    time_of_day: time
    teacher_id: Optional[int]
    teacher_name: Optional[str]
    student_count: int


@dataclass
class PersonRow:
    id: int
    name: str
    role: str
    email: Optional[str]
    facestack_person_id: Optional[int]
    class_count: int


@dataclass
class PersonDetail:
    id: int
    name: str
    role: str
    email: Optional[str]
    facestack_person_id: Optional[int]
    teaches_ids: list[int]      # schedule_ids
    enrolled_ids: list[int]     # schedule_ids
    recent_observations: list[dict]   # for display only


@dataclass
class SnapshotSummary:
    id: int
    schedule_id: int
    class_label: str
    scheduled_date: date
    scheduled_time: time
    actual_time: datetime
    thumbnail_path: Optional[str]
    present_count: int
    total_count: int


# ---------------------------------------------------------------------------
# Room — auto-created, never exposed in the UI. One deployment = one room.
# ---------------------------------------------------------------------------

DEFAULT_CAMERA_URL = "0"   # laptop webcam


def default_room_id(session) -> int:
    """Return the id of the room this deployment uses, creating on first run.

    If no rooms exist we insert "Primary" with camera source = DEFAULT_CAMERA_URL.
    If one or more rooms exist, the lowest-id one wins (stable for a single-room
    deployment; admins with multiple rooms can still edit via the CLI).
    """
    row = session.query(Room).order_by(Room.id).first()
    if row is None:
        row = Room(name="Primary", camera_url=DEFAULT_CAMERA_URL)
        session.add(row)
        session.commit()
    return row.id


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------

def list_classes() -> list[ClassRow]:
    """All classes, ordered by time of day."""
    s = get_session()
    try:
        rows = (
            s.query(Schedule)
            .options(
                joinedload(Schedule.teacher),
                selectinload(Schedule.enrollments),
            )
            .order_by(Schedule.time_of_day)
            .all()
        )
        return [
            ClassRow(
                id=r.id,
                label=r.class_label,
                time_of_day=r.time_of_day,
                teacher_id=r.teacher_id,
                teacher_name=r.teacher.name if r.teacher else None,
                student_count=len(r.enrollments),
            )
            for r in rows
        ]
    finally:
        s.close()


def get_class_detail(class_id: int) -> Optional[dict]:
    """Return a dict with label/time/teacher_id/student_ids for one class."""
    s = get_session()
    try:
        r = (
            s.query(Schedule)
            .options(selectinload(Schedule.enrollments))
            .filter(Schedule.id == class_id)
            .first()
        )
        if r is None:
            return None
        return {
            "id": r.id,
            "label": r.class_label,
            "time_of_day": r.time_of_day,
            "teacher_id": r.teacher_id,
            "student_ids": [e.person_id for e in r.enrollments],
        }
    finally:
        s.close()


def create_class(
    label: str,
    time_of_day: time,
    teacher_id: Optional[int] = None,
    student_ids: Optional[list[int]] = None,
) -> int:
    """Create a new class. Returns its id."""
    label = label.strip()
    if not label:
        raise ValueError("Class name is required.")
    s = get_session()
    try:
        room_id = default_room_id(s)
        sched = Schedule(
            room_id=room_id,
            time_of_day=time_of_day,
            class_label=label,
            teacher_id=teacher_id,
        )
        s.add(sched)
        s.commit()
        if student_ids:
            for sid in student_ids:
                s.add(Enrollment(person_id=sid, schedule_id=sched.id))
            s.commit()
        return sched.id
    finally:
        s.close()


def update_class(
    class_id: int,
    *,
    label: Optional[str] = None,
    time_of_day: Optional[time] = None,
    teacher_id: Optional[int] = ...,     # sentinel: ... means "don't change"
    student_ids: Optional[list[int]] = None,   # None means "don't change"; [] means "empty roster"
) -> None:
    s = get_session()
    try:
        sched = s.query(Schedule).filter(Schedule.id == class_id).first()
        if sched is None:
            raise LookupError(f"class_id={class_id} not found")

        if label is not None:
            l = label.strip()
            if not l:
                raise ValueError("Class name cannot be empty")
            sched.class_label = l
        if time_of_day is not None:
            sched.time_of_day = time_of_day
        if teacher_id is not ...:
            sched.teacher_id = teacher_id

        if student_ids is not None:
            chosen = set(student_ids)
            existing = (
                s.query(Enrollment)
                .filter(Enrollment.schedule_id == class_id)
                .all()
            )
            existing_pids = {e.person_id for e in existing}
            for e in existing:
                if e.person_id not in chosen:
                    s.delete(e)
            for pid in chosen - existing_pids:
                s.add(Enrollment(person_id=pid, schedule_id=class_id))
        s.commit()
    finally:
        s.close()


def delete_class(class_id: int) -> None:
    s = get_session()
    try:
        sched = s.query(Schedule).filter(Schedule.id == class_id).first()
        if sched is None:
            return
        s.delete(sched)  # cascades to enrollments + snapshots
        s.commit()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------

def list_people() -> list[PersonRow]:
    s = get_session()
    try:
        rows = (
            s.query(Person)
            .options(selectinload(Person.enrollments), selectinload(Person.teaches))
            .order_by(Person.name)
            .all()
        )
        out = []
        for p in rows:
            class_count = (
                len(p.enrollments) if p.role == "student"
                else len(p.teaches) if p.role == "teacher"
                else 0
            )
            out.append(PersonRow(
                id=p.id, name=p.name, role=p.role, email=p.email,
                facestack_person_id=p.facestack_person_id,
                class_count=class_count,
            ))
        return out
    finally:
        s.close()


def get_person_detail(person_id: int) -> Optional[PersonDetail]:
    s = get_session()
    try:
        p = (
            s.query(Person)
            .options(
                selectinload(Person.enrollments),
                selectinload(Person.teaches),
            )
            .filter(Person.id == person_id)
            .first()
        )
        if p is None:
            return None

        recent = (
            s.query(Observation)
            .options(
                joinedload(Observation.snapshot).joinedload(Snapshot.schedule),
            )
            .filter(Observation.person_id == person_id)
            .order_by(Observation.id.desc())
            .limit(10)
            .all()
        )
        recent_dicts = [
            {
                "date": str(o.snapshot.scheduled_date),
                "time": o.snapshot.scheduled_time.strftime("%H:%M"),
                "class": o.snapshot.schedule.class_label,
                "seen": f"{o.frames_seen}/{o.total_frames}",
                "score": f"{o.avg_score:.2f}",
                "status": "Present" if o.is_present else "Absent",
            }
            for o in recent
        ]

        return PersonDetail(
            id=p.id, name=p.name, role=p.role, email=p.email,
            facestack_person_id=p.facestack_person_id,
            teaches_ids=[s.id for s in p.teaches],
            enrolled_ids=[e.schedule_id for e in p.enrollments],
            recent_observations=recent_dicts,
        )
    finally:
        s.close()


def update_person(
    person_id: int,
    *,
    name: Optional[str] = None,
    role: Optional[str] = None,
    email: Optional[str] = ...,
) -> None:
    s = get_session()
    try:
        p = s.query(Person).filter(Person.id == person_id).first()
        if p is None:
            raise LookupError(f"person_id={person_id} not found")
        if name is not None:
            n = name.strip()
            if not n:
                raise ValueError("Name cannot be empty")
            p.name = n
        if role is not None:
            if role not in ("student", "teacher", "admin"):
                raise ValueError(f"Unknown role {role!r}")
            p.role = role
        if email is not ...:
            p.email = email or None
        s.commit()
    finally:
        s.close()


def delete_person(person_id: int) -> None:
    s = get_session()
    try:
        p = s.query(Person).filter(Person.id == person_id).first()
        if p is None:
            return
        s.delete(p)  # cascades to enrollments + observations
        s.commit()
    finally:
        s.close()


def set_person_classes(person_id: int, class_ids: list[int]) -> None:
    """Set the complete class list for a person. Interpretation depends on role.

    Students: enrollments match the list exactly.
    Teachers: person becomes the teacher on every listed class (displacing
              anyone currently assigned there), and is unassigned from any
              class they were teaching that isn't in the list.
    Admin: no-op.
    """
    s = get_session()
    try:
        p = s.query(Person).filter(Person.id == person_id).first()
        if p is None:
            raise LookupError(f"person_id={person_id} not found")
        chosen = set(class_ids)

        if p.role == "student":
            existing = (
                s.query(Enrollment).filter(Enrollment.person_id == person_id).all()
            )
            existing_sids = {e.schedule_id for e in existing}
            for e in existing:
                if e.schedule_id not in chosen:
                    s.delete(e)
            for sid in chosen - existing_sids:
                s.add(Enrollment(person_id=person_id, schedule_id=sid))
        elif p.role == "teacher":
            currently_teaching = (
                s.query(Schedule).filter(Schedule.teacher_id == person_id).all()
            )
            for sched in currently_teaching:
                if sched.id not in chosen:
                    sched.teacher_id = None
            for sid in chosen:
                sched = s.query(Schedule).filter(Schedule.id == sid).one()
                sched.teacher_id = person_id
        s.commit()
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------

def capture_now(
    class_id: int,
    db_url: Optional[str] = None,
    pipeline=None,
    show_preview: bool = True,
) -> Optional[int]:
    """Fire one sampling cycle for a class immediately. Returns Snapshot.id.

    Same machinery the scheduler daemon uses — just invoked by a human
    clicking a button instead of by cron. Useful for camera positioning,
    demos, and testing enrollment without waiting for the next period.
    """
    from classcheck.scheduler import run_sampling

    return run_sampling(
        schedule_id=class_id,
        db_url=db_url,
        pipeline=pipeline,
        show_preview=show_preview,
    )


def list_snapshots_for_date(on_date: date,
                             class_id: Optional[int] = None) -> list[SnapshotSummary]:
    """All snapshots captured on `on_date`, optionally filtered to one class."""
    s = get_session()
    try:
        q = (
            s.query(Snapshot)
            .options(
                joinedload(Snapshot.schedule),
                selectinload(Snapshot.observations),
            )
            .filter(Snapshot.scheduled_date == on_date)
            .order_by(Snapshot.scheduled_time)
        )
        if class_id is not None:
            q = q.filter(Snapshot.schedule_id == class_id)
        rows = q.all()
        return [
            SnapshotSummary(
                id=r.id,
                schedule_id=r.schedule_id,
                class_label=r.schedule.class_label,
                scheduled_date=r.scheduled_date,
                scheduled_time=r.scheduled_time,
                actual_time=r.actual_time,
                thumbnail_path=r.thumbnail_path,
                present_count=sum(1 for o in r.observations if o.is_present),
                total_count=len(r.observations),
            )
            for r in rows
        ]
    finally:
        s.close()


def get_snapshot_detail(snapshot_id: int) -> Optional[dict]:
    """Per-person attendance rows for a specific snapshot."""
    s = get_session()
    try:
        snap = (
            s.query(Snapshot)
            .options(
                joinedload(Snapshot.schedule),
                selectinload(Snapshot.observations).joinedload(Observation.person),
            )
            .filter(Snapshot.id == snapshot_id)
            .first()
        )
        if snap is None:
            return None
        return {
            "id": snap.id,
            "schedule_id": snap.schedule_id,
            "class_label": snap.schedule.class_label,
            "scheduled_date": snap.scheduled_date,
            "scheduled_time": snap.scheduled_time,
            "actual_time": snap.actual_time,
            "n_frames": snap.n_frames,
            "thumbnail_path": snap.thumbnail_path,
            "observations": [
                {
                    "person_id": o.person_id,
                    "name": o.person.name,
                    "role": o.person.role,
                    "frames_seen": o.frames_seen,
                    "total_frames": o.total_frames,
                    "avg_score": o.avg_score,
                    "is_present": o.is_present,
                }
                for o in sorted(snap.observations, key=lambda x: x.person.name)
            ],
        }
    finally:
        s.close()
