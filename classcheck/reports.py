"""Reporting layer — turn Snapshot/Observation rows into CSV deliverables.

Three reports, three audiences:

  1. daily_student_roll(class, date)   → teacher / parents
  2. daily_teacher_attendance(date)    → principal / admin
  3. monthly_summary(class, year, month) → principal / teacher

Each returns a CSV string. Callers decide what to do with it
(write to disk, email, upload, stream to HTTP response, etc.).
"""

import csv
import io
from calendar import monthrange
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from classcheck.models import (
    Observation,
    Person,
    Schedule,
    Snapshot,
)


# --- Helpers ---

def _schedule_by_label(session: Session, class_label: str) -> Optional[Schedule]:
    return session.query(Schedule).filter(Schedule.class_label == class_label).first()


# --- Reports ---

def daily_student_roll(
    session: Session,
    class_label: str,
    on_date: date,
) -> str:
    """CSV: one row per student in this class, showing presence on this date.

    Columns: Name, Role, Slot, Scheduled, Seen, Avg Score, Status, Captured
    """
    sched = _schedule_by_label(session, class_label)
    if sched is None:
        raise ValueError(f"Class {class_label!r} not found")

    snap = (
        session.query(Snapshot)
        .filter(Snapshot.schedule_id == sched.id, Snapshot.scheduled_date == on_date)
        .order_by(Snapshot.actual_time.desc())
        .first()
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Name", "Role", "Class", "Scheduled Time",
        "Frames Seen", "Total Frames", "Avg Score", "Status", "Captured At",
    ])

    enrolled_students = [e.person for e in sched.enrollments]

    if snap is None:
        # No capture → everyone listed as "not captured"
        for p in enrolled_students:
            writer.writerow([
                p.name, p.role, class_label, sched.time_of_day,
                0, 0, 0.0, "NOT_CAPTURED", "",
            ])
        return buf.getvalue()

    obs_by_person = {
        o.person_id: o for o in session.query(Observation).filter(Observation.snapshot_id == snap.id).all()
    }

    for p in enrolled_students:
        o = obs_by_person.get(p.id)
        if o is None:
            writer.writerow([
                p.name, p.role, class_label, sched.time_of_day,
                0, snap.n_frames, 0.0, "NOT_OBSERVED", snap.actual_time.isoformat(timespec="seconds"),
            ])
        else:
            writer.writerow([
                p.name, p.role, class_label, sched.time_of_day,
                o.frames_seen, o.total_frames, f"{o.avg_score:.2f}",
                "PRESENT" if o.is_present else "ABSENT",
                snap.actual_time.isoformat(timespec="seconds"),
            ])
    return buf.getvalue()


def daily_teacher_attendance(session: Session, on_date: date) -> str:
    """CSV: one row per (teacher, schedule) pair on this date.

    Columns: Teacher, Class, Room, Scheduled Time, Seen, Status, Captured At
    Only includes schedules that have a teacher assigned.
    """
    schedules_with_teachers = (
        session.query(Schedule).filter(Schedule.teacher_id.isnot(None)).all()
    )

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Teacher", "Class", "Room", "Scheduled Time",
        "Frames Seen", "Total Frames", "Avg Score", "Status", "Captured At",
    ])

    for sched in schedules_with_teachers:
        teacher = sched.teacher
        snap = (
            session.query(Snapshot)
            .filter(Snapshot.schedule_id == sched.id, Snapshot.scheduled_date == on_date)
            .order_by(Snapshot.actual_time.desc())
            .first()
        )

        if snap is None:
            writer.writerow([
                teacher.name, sched.class_label, sched.room.name, sched.time_of_day,
                0, 0, 0.0, "NOT_CAPTURED", "",
            ])
            continue

        obs = (
            session.query(Observation)
            .filter(Observation.snapshot_id == snap.id, Observation.person_id == teacher.id)
            .first()
        )
        if obs is None:
            writer.writerow([
                teacher.name, sched.class_label, sched.room.name, sched.time_of_day,
                0, snap.n_frames, 0.0, "NOT_OBSERVED",
                snap.actual_time.isoformat(timespec="seconds"),
            ])
        else:
            writer.writerow([
                teacher.name, sched.class_label, sched.room.name, sched.time_of_day,
                obs.frames_seen, obs.total_frames, f"{obs.avg_score:.2f}",
                "PRESENT" if obs.is_present else "ABSENT",
                snap.actual_time.isoformat(timespec="seconds"),
            ])
    return buf.getvalue()


def monthly_summary(
    session: Session,
    class_label: str,
    year: int,
    month: int,
) -> str:
    """CSV: per-student monthly aggregate for one class.

    Columns: Name, Role, Sessions Held, Sessions Present, Attendance %
    """
    sched = _schedule_by_label(session, class_label)
    if sched is None:
        raise ValueError(f"Class {class_label!r} not found")

    first = date(year, month, 1)
    last = date(year, month, monthrange(year, month)[1])

    snapshots = (
        session.query(Snapshot)
        .filter(
            Snapshot.schedule_id == sched.id,
            Snapshot.scheduled_date >= first,
            Snapshot.scheduled_date <= last,
        )
        .all()
    )
    sessions_held = len(snapshots)
    snap_ids = [s.id for s in snapshots]

    enrolled_students = [e.person for e in sched.enrollments]

    # Compute per-person present counts
    per_person_present: dict[int, int] = {}
    if snap_ids:
        rows = (
            session.query(Observation)
            .filter(
                Observation.snapshot_id.in_(snap_ids),
                Observation.is_present.is_(True),
            )
            .all()
        )
        for o in rows:
            per_person_present[o.person_id] = per_person_present.get(o.person_id, 0) + 1

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Name", "Role", "Class", "Month",
        "Sessions Held", "Sessions Present", "Attendance %",
    ])
    month_str = f"{year:04d}-{month:02d}"
    for p in enrolled_students:
        present = per_person_present.get(p.id, 0)
        pct = (present / sessions_held * 100.0) if sessions_held else 0.0
        writer.writerow([
            p.name, p.role, class_label, month_str,
            sessions_held, present, f"{pct:.1f}%",
        ])
    return buf.getvalue()


# --- Date helpers for CLI ---

def parse_date(s: str) -> date:
    """Accept YYYY-MM-DD or 'today' or 'yesterday'."""
    s = s.strip().lower()
    if s == "today":
        return date.today()
    if s == "yesterday":
        return date.today() - timedelta(days=1)
    y, m, d = s.split("-")
    return date(int(y), int(m), int(d))
