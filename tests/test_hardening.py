"""Day 6 tests: hardening — camera failures, timeouts, scheduler resilience."""

from unittest.mock import MagicMock, patch

import pytest

from classcheck import core
from classcheck.core import capture_burst


@patch("cv2.VideoCapture")
def test_capture_burst_raises_if_camera_unopenable(mock_cap_class):
    """If VideoCapture can't open the source, raise RuntimeError."""
    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = False
    mock_cap_class.return_value = mock_cap

    with pytest.raises(RuntimeError, match="Could not open camera"):
        capture_burst(camera_source="bogus", n_frames=3, duration_s=0.1, warmup_s=0)
    mock_cap.release.assert_called_once()


@patch("cv2.VideoCapture")
def test_capture_burst_skips_failed_reads_but_returns_good_ones(mock_cap_class):
    """cap.read() failures are skipped; only successful reads can get captured.

    The new loop-continuously design reads as fast as it can until n_frames
    are captured or the timeout hits. So we give read() an INFINITE generator
    that alternates failure / success, and assert that we eventually capture
    the requested count.
    """
    import numpy as np

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    fake_frame = np.zeros((10, 10, 3), dtype=np.uint8)

    def alternating_reads():
        i = 0
        while True:
            i += 1
            yield (False, None) if i % 2 else (True, fake_frame)

    mock_cap.read.side_effect = alternating_reads()
    mock_cap_class.return_value = mock_cap

    frames = capture_burst(camera_source="0", n_frames=3, duration_s=0.05, warmup_s=0)
    assert len(frames) == 3
    mock_cap.release.assert_called_once()


@patch("cv2.VideoCapture")
def test_capture_burst_honours_hard_timeout(mock_cap_class):
    """If reads are so slow that hard_timeout_s expires, we bail out early."""
    import numpy as np

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True

    # Simulate reads that succeed but take so long that we hit the timeout.
    import time

    def slow_read():
        time.sleep(0.15)
        return (True, np.zeros((10, 10, 3), dtype=np.uint8))

    mock_cap.read.side_effect = lambda: slow_read()
    mock_cap_class.return_value = mock_cap

    frames = capture_burst(
        camera_source="0", n_frames=100, duration_s=10.0, hard_timeout_s=0.3, warmup_s=0,
    )
    # We should have been stopped before capturing all 100
    assert len(frames) < 100
    mock_cap.release.assert_called_once()


def test_run_sampling_swallows_recognition_exception(tmp_path, monkeypatch):
    """A thrown attendance_check does NOT crash run_sampling; returns None."""
    from datetime import time

    from classcheck.models import (
        Enrollment, Person, Room, Schedule, Snapshot, get_session, init_db,
    )
    from classcheck.scheduler import run_sampling

    db_url = f"sqlite:///{tmp_path}/test.db"
    init_db(db_url)
    s = get_session(db_url)
    try:
        alice = Person(name="Alice", role="student", facestack_person_id=1)
        s.add(alice); s.commit()
        room = Room(name="R", camera_url="0")
        s.add(room); s.commit()
        sched = Schedule(room_id=room.id, time_of_day=time(11, 0), class_label="X")
        s.add(sched); s.commit()
        s.add(Enrollment(person_id=alice.id, schedule_id=sched.id)); s.commit()
        schedule_id = sched.id
    finally:
        s.close()

    # capture_burst returns frames, but attendance_check blows up
    import numpy as np
    monkeypatch.setattr("classcheck.scheduler.capture_burst",
                        lambda **kw: [np.zeros((10, 10, 3), dtype=np.uint8)])

    def boom(*args, **kwargs):
        raise RuntimeError("model blew up")
    monkeypatch.setattr("classcheck.scheduler.attendance_check", boom)

    snap_id = run_sampling(schedule_id, db_url, pipeline=MagicMock())
    assert snap_id is None

    # No snapshot was written
    s = get_session(db_url)
    try:
        assert s.query(Snapshot).count() == 0
    finally:
        s.close()


def test_run_sampling_returns_none_on_camera_failure(tmp_path, monkeypatch):
    """If capture_burst raises RuntimeError, run_sampling returns None cleanly."""
    from datetime import time

    from classcheck.models import (
        Person, Room, Schedule, Snapshot, get_session, init_db,
    )
    from classcheck.scheduler import run_sampling

    db_url = f"sqlite:///{tmp_path}/test.db"
    init_db(db_url)
    s = get_session(db_url)
    try:
        room = Room(name="R", camera_url="bogus")
        s.add(room); s.commit()
        sched = Schedule(room_id=room.id, time_of_day=time(11, 0), class_label="X")
        s.add(sched); s.commit()
        schedule_id = sched.id
    finally:
        s.close()

    def broken_camera(**kw):
        raise RuntimeError("Could not open camera")
    monkeypatch.setattr("classcheck.scheduler.capture_burst", broken_camera)

    snap_id = run_sampling(schedule_id, db_url, pipeline=MagicMock())
    assert snap_id is None
