"""Smoke tests for the FastAPI layer.

Every route that doesn't require the face recognition pipeline is
exercised end-to-end through a TestClient against an isolated per-test
SQLite DB. Enroll and capture are covered by scheduler / enroll tests
in their own files; here we only assert the HTTP wrapper shape.
"""

from datetime import date, time

import pytest
from fastapi.testclient import TestClient

from classcheck import api as api_mod
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


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Point both the API's DB lookups AND every services.* call at a
    # per-test sandbox so tests can't touch ~/.classcheck.
    monkeypatch.setenv("CLASSCHECK_HOME", str(tmp_path))
    db_url = f"sqlite:///{tmp_path / 'classcheck.db'}"
    init_db(db_url)

    s = get_session(db_url)
    try:
        s.add(Room(name="Primary", camera_url="0"))
        s.commit()
    finally:
        s.close()

    # Don't let tests accidentally load ONNX.
    monkeypatch.setattr(api_mod, "_get_pipeline", lambda: None)

    with TestClient(api_mod.app) as c:
        yield c, db_url


def test_health(client):
    c, _ = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_classes_crud(client):
    c, _ = client

    # List is initially empty.
    assert c.get("/classes").json() == []

    # Create.
    r = c.post(
        "/classes",
        json={"label": "Math-10A", "time_of_day": "11:00"},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["label"] == "Math-10A"
    assert r.json()["time_of_day"] == "11:00"

    # List now has one.
    assert len(c.get("/classes").json()) == 1

    # Get by id.
    r = c.get(f"/classes/{cid}")
    assert r.status_code == 200
    assert r.json()["id"] == cid

    # Patch time and label.
    r = c.patch(
        f"/classes/{cid}",
        json={"label": "Physics-10", "time_of_day": "12:00"},
    )
    assert r.status_code == 200
    assert r.json()["label"] == "Physics-10"
    assert r.json()["time_of_day"] == "12:00"

    # Delete.
    r = c.delete(f"/classes/{cid}")
    assert r.status_code == 204
    assert c.get("/classes").json() == []


def test_class_not_found(client):
    c, _ = client
    assert c.get("/classes/999").status_code == 404
    assert c.patch("/classes/999", json={"label": "x"}).status_code == 404


def test_people_crud(client):
    c, db_url = client

    # Create a person directly (enrollment needs the pipeline; we're
    # testing the HTTP wrapper around list/get/update/delete).
    s = get_session(db_url)
    try:
        p = Person(name="Alice", role="student", email="a@example.com", facestack_person_id=1)
        s.add(p)
        s.commit()
        pid = p.id
    finally:
        s.close()

    r = c.get("/people")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["name"] == "Alice"

    r = c.get(f"/people/{pid}")
    assert r.status_code == 200
    assert r.json()["email"] == "a@example.com"

    # Update email to None via explicit null.
    r = c.patch(f"/people/{pid}", json={"email": None})
    assert r.status_code == 200
    assert r.json()["email"] is None

    # Delete.
    r = c.delete(f"/people/{pid}")
    assert r.status_code == 204
    assert c.get("/people").json() == []


def test_set_person_classes(client):
    c, db_url = client

    # Build class + student directly.
    s = get_session(db_url)
    try:
        room = s.query(Room).first()
        sched = Schedule(
            room_id=room.id, time_of_day=time(9, 0), class_label="Math-10A"
        )
        student = Person(name="Bob", role="student", facestack_person_id=2)
        s.add_all([sched, student])
        s.commit()
        cid, pid = sched.id, student.id
    finally:
        s.close()

    # Assign.
    r = c.put(f"/people/{pid}/classes", json={"class_ids": [cid]})
    assert r.status_code == 200
    assert cid in r.json()["enrolled_ids"]

    # Clear.
    r = c.put(f"/people/{pid}/classes", json={"class_ids": []})
    assert r.status_code == 200
    assert r.json()["enrolled_ids"] == []


def test_snapshots_list_and_detail(client):
    c, db_url = client
    today = date.today()

    # Seed one snapshot with one observation.
    s = get_session(db_url)
    try:
        room = s.query(Room).first()
        sched = Schedule(
            room_id=room.id, time_of_day=time(11, 0), class_label="Physics-10"
        )
        person = Person(name="Dana", role="student", facestack_person_id=10)
        s.add_all([sched, person])
        s.commit()
        from datetime import datetime

        snap = Snapshot(
            schedule_id=sched.id,
            scheduled_date=today,
            scheduled_time=time(11, 0),
            actual_time=datetime.now(),
            n_frames=10,
        )
        s.add(snap)
        s.commit()
        s.add(
            Observation(
                snapshot_id=snap.id,
                person_id=person.id,
                frames_seen=7,
                total_frames=10,
                avg_score=0.82,
                is_present=True,
            )
        )
        s.commit()
        snap_id = snap.id
    finally:
        s.close()

    r = c.get(f"/snapshots?date={today.isoformat()}")
    assert r.status_code == 200
    assert len(r.json()) == 1
    assert r.json()[0]["present_count"] == 1

    r = c.get(f"/snapshots/{snap_id}")
    assert r.status_code == 200
    obs = r.json()["observations"]
    assert len(obs) == 1
    assert obs[0]["is_present"] is True


def test_snapshot_csv_downloads(client):
    """Roll and teacher CSVs end to end."""
    c, db_url = client
    today = date.today()

    s = get_session(db_url)
    try:
        room = s.query(Room).first()
        teacher = Person(name="Mr Sharma", role="teacher", facestack_person_id=200)
        student = Person(name="Alice", role="student", facestack_person_id=100)
        s.add_all([teacher, student])
        s.commit()
        sched = Schedule(
            room_id=room.id,
            time_of_day=time(11, 0),
            class_label="Physics-10",
            teacher_id=teacher.id,
        )
        s.add(sched)
        s.commit()
        s.add(Enrollment(person_id=student.id, schedule_id=sched.id))
        s.commit()
        from datetime import datetime

        snap = Snapshot(
            schedule_id=sched.id,
            scheduled_date=today,
            scheduled_time=time(11, 0),
            actual_time=datetime.now(),
            n_frames=10,
        )
        s.add(snap)
        s.commit()
        snap_id = snap.id
    finally:
        s.close()

    r = c.get(f"/snapshots/{snap_id}/roll.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "Alice" in r.text

    r = c.get(f"/snapshots/{snap_id}/teachers.csv")
    assert r.status_code == 200
    assert "Mr Sharma" in r.text


def test_enroll_validates_inputs(client):
    """The enroll route rejects obvious bad input without needing a pipeline."""
    c, _ = client
    # Missing photos → 422 from FastAPI validation.
    r = c.post("/enroll", data={"name": "X", "role": "student"})
    assert r.status_code == 422

    # Unknown role.
    r = c.post(
        "/enroll",
        data={"name": "X", "role": "alien"},
        files={"photos": ("x.jpg", b"fake", "image/jpeg")},
    )
    assert r.status_code == 400


def test_capture_on_missing_class(client):
    c, _ = client
    r = c.post("/classes/99999/capture?show_preview=false")
    assert r.status_code == 404
