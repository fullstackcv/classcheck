"""Day 5 tests: report CSVs derive correctly from seeded DB state."""

from datetime import date, datetime, time

import pytest

from classcheck.models import (
    Enrollment,
    Observation,
    Person,
    Room,
    Schedule,
    Snapshot,
    get_session,
    init_db,
)
from classcheck.reports import (
    daily_student_roll,
    daily_teacher_attendance,
    monthly_summary,
    parse_date,
)


@pytest.fixture
def seeded(tmp_path):
    """Seed a DB with one class, one teacher, three students, and three snapshots
    across two dates — enough to exercise each report type.
    """
    db_file = tmp_path / "reports.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url)
    s = get_session(db_url)

    # Persons
    teacher = Person(name="Sharma", role="teacher", facestack_person_id=1)
    alice = Person(name="Alice", role="student", facestack_person_id=2)
    bob = Person(name="Bob", role="student", facestack_person_id=3)
    carol = Person(name="Carol", role="student", facestack_person_id=4)
    s.add_all([teacher, alice, bob, carol])
    s.commit()

    room = Room(name="R1", camera_url="0")
    s.add(room); s.commit()

    sched = Schedule(
        room_id=room.id,
        time_of_day=time(11, 0),
        class_label="Math-10A",
        teacher_id=teacher.id,
    )
    s.add(sched); s.commit()

    for student in (alice, bob, carol):
        s.add(Enrollment(person_id=student.id, schedule_id=sched.id))
    s.commit()

    today = date(2026, 4, 17)
    yesterday = date(2026, 4, 16)

    # Snapshot 1 (today): Alice present, Bob present, Carol absent, teacher present
    snap1 = Snapshot(
        schedule_id=sched.id, scheduled_date=today, scheduled_time=time(11, 0),
        actual_time=datetime(2026, 4, 17, 11, 0, 30), n_frames=10,
    )
    s.add(snap1); s.commit()
    s.add_all([
        Observation(snapshot_id=snap1.id, person_id=alice.id,   frames_seen=8, total_frames=10, avg_score=0.85, is_present=True),
        Observation(snapshot_id=snap1.id, person_id=bob.id,     frames_seen=5, total_frames=10, avg_score=0.70, is_present=True),
        Observation(snapshot_id=snap1.id, person_id=carol.id,   frames_seen=1, total_frames=10, avg_score=0.40, is_present=False),
        Observation(snapshot_id=snap1.id, person_id=teacher.id, frames_seen=9, total_frames=10, avg_score=0.90, is_present=True),
    ])
    s.commit()

    # Snapshot 2 (yesterday): all three students present, teacher absent
    snap2 = Snapshot(
        schedule_id=sched.id, scheduled_date=yesterday, scheduled_time=time(11, 0),
        actual_time=datetime(2026, 4, 16, 11, 0, 30), n_frames=10,
    )
    s.add(snap2); s.commit()
    s.add_all([
        Observation(snapshot_id=snap2.id, person_id=alice.id,   frames_seen=9, total_frames=10, avg_score=0.88, is_present=True),
        Observation(snapshot_id=snap2.id, person_id=bob.id,     frames_seen=7, total_frames=10, avg_score=0.75, is_present=True),
        Observation(snapshot_id=snap2.id, person_id=carol.id,   frames_seen=6, total_frames=10, avg_score=0.72, is_present=True),
        Observation(snapshot_id=snap2.id, person_id=teacher.id, frames_seen=1, total_frames=10, avg_score=0.35, is_present=False),
    ])
    s.commit()

    s.close()
    return {"db_url": db_url, "today": today, "yesterday": yesterday}


def test_daily_student_roll_reports_each_student(seeded):
    s = get_session(seeded["db_url"])
    try:
        csv = daily_student_roll(s, "Math-10A", seeded["today"])
    finally:
        s.close()

    lines = csv.strip().split("\n")
    assert lines[0].startswith("Name,Role,Class")
    # 3 students enrolled + header = 4 lines
    assert len(lines) == 4
    assert "Alice" in csv and "Bob" in csv and "Carol" in csv
    # Carol should be ABSENT today (0.40 below threshold)
    carol_row = next(l for l in lines if l.startswith("Carol"))
    assert "ABSENT" in carol_row
    # Alice should be PRESENT
    alice_row = next(l for l in lines if l.startswith("Alice"))
    assert "PRESENT" in alice_row


def test_daily_student_roll_when_no_snapshot(seeded):
    s = get_session(seeded["db_url"])
    try:
        csv = daily_student_roll(s, "Math-10A", date(2026, 4, 15))  # no snapshot that day
    finally:
        s.close()
    # Every enrolled student should get a NOT_CAPTURED row
    assert csv.count("NOT_CAPTURED") == 3


def test_daily_teacher_attendance(seeded):
    s = get_session(seeded["db_url"])
    try:
        csv = daily_teacher_attendance(s, seeded["today"])
    finally:
        s.close()

    assert "Sharma" in csv
    assert "PRESENT" in csv  # was present today
    # Yesterday the same teacher was absent
    s = get_session(seeded["db_url"])
    try:
        csv_yesterday = daily_teacher_attendance(s, seeded["yesterday"])
    finally:
        s.close()
    assert "ABSENT" in csv_yesterday


def test_monthly_summary_counts_sessions_and_present(seeded):
    s = get_session(seeded["db_url"])
    try:
        csv = monthly_summary(s, "Math-10A", 2026, 4)
    finally:
        s.close()

    lines = csv.strip().split("\n")
    # Header + 3 students
    assert len(lines) == 4
    # 2 sessions this month (today + yesterday). Alice: 2/2 = 100%. Carol: 1/2 = 50%.
    alice_row = next(l for l in lines if l.startswith("Alice"))
    assert "2,2,100.0%" in alice_row
    carol_row = next(l for l in lines if l.startswith("Carol"))
    assert "2,1,50.0%" in carol_row


def test_monthly_summary_zero_sessions(seeded):
    """If no sessions held that month, everyone gets 0/0 and 0.0%."""
    s = get_session(seeded["db_url"])
    try:
        csv = monthly_summary(s, "Math-10A", 2025, 1)  # far-past month, no data
    finally:
        s.close()
    assert "0,0,0.0%" in csv


def test_parse_date_formats():
    assert parse_date("2026-04-17") == date(2026, 4, 17)
    assert parse_date("today") == date.today()
    assert parse_date("YESTERDAY") is not None


def test_missing_class_raises(seeded):
    s = get_session(seeded["db_url"])
    try:
        with pytest.raises(ValueError, match="not found"):
            daily_student_roll(s, "Nonexistent-Class", seeded["today"])
    finally:
        s.close()
