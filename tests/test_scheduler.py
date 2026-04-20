"""Day 3 tests: run_sampling writes correct rows under mocked capture + vote."""

from datetime import time
from unittest.mock import MagicMock

import numpy as np
import pytest

from classcheck.core import VoteResult
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
from classcheck.scheduler import build_scheduler, run_sampling


@pytest.fixture
def seeded_db(tmp_path, monkeypatch):
    """In-memory-ish DB seeded with a room, schedule, and roster."""
    db_file = tmp_path / "test.db"
    db_url = f"sqlite:///{db_file}"
    init_db(db_url)

    session = get_session(db_url)
    try:
        # Three persons: 2 students + 1 teacher
        alice = Person(name="Alice", role="student", facestack_person_id=100)
        bob = Person(name="Bob", role="student", facestack_person_id=101)
        sharma = Person(name="Mr Sharma", role="teacher", facestack_person_id=200)
        session.add_all([alice, bob, sharma])
        session.commit()
        ids = {"alice": alice.id, "bob": bob.id, "sharma": sharma.id}

        room = Room(name="Room 101", camera_url="0")
        session.add(room)
        session.commit()
        room_id = room.id

        sched = Schedule(
            room_id=room_id,
            time_of_day=time(11, 0),
            class_label="Math-10A",
            teacher_id=sharma.id,
        )
        session.add(sched)
        session.commit()
        schedule_id = sched.id

        session.add(Enrollment(person_id=alice.id, schedule_id=sched.id))
        session.add(Enrollment(person_id=bob.id, schedule_id=sched.id))
        session.commit()
    finally:
        session.close()

    return {
        "db_url": db_url,
        "schedule_id": schedule_id,
        "ids": ids,
    }


def test_run_sampling_writes_snapshot_and_observations(seeded_db, monkeypatch):
    """Alice present (8/10), Bob absent (0/10), teacher present (7/10). Write 3 obs rows."""
    fake_frames = [np.zeros((10, 10, 3), dtype=np.uint8) for _ in range(10)]
    fake_votes = [
        VoteResult(facestack_person_id=100, frames_seen=8, total_frames=10, avg_score=0.85, is_present=True),
        VoteResult(facestack_person_id=101, frames_seen=0, total_frames=10, avg_score=0.0, is_present=False),
        VoteResult(facestack_person_id=200, frames_seen=7, total_frames=10, avg_score=0.80, is_present=True),
    ]

    # Patch where the scheduler looks these names up
    monkeypatch.setattr("classcheck.scheduler.capture_burst", lambda **kw: fake_frames)
    monkeypatch.setattr("classcheck.scheduler.attendance_check", lambda *a, **kw: fake_votes)

    snap_id = run_sampling(seeded_db["schedule_id"], seeded_db["db_url"], pipeline=MagicMock())
    assert snap_id is not None

    session = get_session(seeded_db["db_url"])
    try:
        snap = session.query(Snapshot).filter(Snapshot.id == snap_id).one()
        assert snap.n_frames == 10
        assert snap.scheduled_time == time(11, 0)

        obs = session.query(Observation).filter(Observation.snapshot_id == snap_id).all()
        assert len(obs) == 3

        by_pid = {o.person_id: o for o in obs}
        assert by_pid[seeded_db["ids"]["alice"]].is_present is True
        assert by_pid[seeded_db["ids"]["alice"]].frames_seen == 8
        assert abs(by_pid[seeded_db["ids"]["alice"]].avg_score - 0.85) < 1e-6
        assert by_pid[seeded_db["ids"]["bob"]].is_present is False
        assert by_pid[seeded_db["ids"]["bob"]].frames_seen == 0
        assert by_pid[seeded_db["ids"]["sharma"]].is_present is True
    finally:
        session.close()


def test_run_sampling_skips_if_no_frames(seeded_db, monkeypatch):
    """If capture_burst returns [], no snapshot is written."""
    monkeypatch.setattr("classcheck.scheduler.capture_burst", lambda **kw: [])
    monkeypatch.setattr("classcheck.scheduler.attendance_check", lambda *a, **kw: [])

    snap_id = run_sampling(seeded_db["schedule_id"], seeded_db["db_url"], pipeline=MagicMock())
    assert snap_id is None

    session = get_session(seeded_db["db_url"])
    try:
        assert session.query(Snapshot).count() == 0
        assert session.query(Observation).count() == 0
    finally:
        session.close()


def test_run_sampling_ignores_unknown_recognized_persons(seeded_db, monkeypatch):
    """A recognized person not on this schedule's roster is ignored — no obs row."""
    fake_frames = [np.zeros((10, 10, 3), dtype=np.uint8)]
    fake_votes = [
        VoteResult(facestack_person_id=100, frames_seen=5, total_frames=1, avg_score=0.9, is_present=True),
        VoteResult(facestack_person_id=999, frames_seen=4, total_frames=1, avg_score=0.88, is_present=True),
    ]

    monkeypatch.setattr("classcheck.scheduler.capture_burst", lambda **kw: fake_frames)
    monkeypatch.setattr("classcheck.scheduler.attendance_check", lambda *a, **kw: fake_votes)

    snap_id = run_sampling(seeded_db["schedule_id"], seeded_db["db_url"], pipeline=MagicMock())

    session = get_session(seeded_db["db_url"])
    try:
        obs = session.query(Observation).filter(Observation.snapshot_id == snap_id).all()
        person_ids = {o.person_id for o in obs}
        # Alice (100) should be in; 999 not in roster so its vote was ignored.
        assert seeded_db["ids"]["alice"] in person_ids
        # No orphan observation rows created for facestack_person_id=999
        assert all(o.person_id in {
            seeded_db["ids"]["alice"],
            seeded_db["ids"]["bob"],
            seeded_db["ids"]["sharma"],
        } for o in obs)
    finally:
        session.close()


def test_build_scheduler_registers_one_job_per_schedule(seeded_db):
    """build_scheduler should install one cron job per Schedule row."""
    sched = build_scheduler(seeded_db["db_url"])
    jobs = sched.get_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == f"sample-{seeded_db['schedule_id']}"
