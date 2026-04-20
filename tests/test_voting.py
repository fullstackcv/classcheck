"""Day 1 test: voting algorithm behaves correctly on synthetic inputs.

We don't need a real camera for this test. We fake the pipeline output
and verify the vote math is right.
"""

from unittest.mock import MagicMock, patch

import numpy as np

from classcheck.core import attendance_check


def _fake_frame():
    """A dummy BGR frame — contents don't matter since we mock the pipeline."""
    return np.zeros((100, 100, 3), dtype=np.uint8)


def _mock_result(person_id, score, bbox=(0, 0, 10, 10), confidence=0.9):
    """Build a mock FaceResult-like object with just the fields we use."""
    r = MagicMock()
    r.person_id = person_id
    r.recognition_score = score
    r.bbox = bbox
    r.confidence = confidence
    return r


def test_person_seen_in_enough_frames_is_present():
    """Alice (id=1) appears in 5 of 10 frames at score 0.8 — should be PRESENT."""
    frames = [_fake_frame() for _ in range(10)]

    # Build per-frame outputs: Alice seen in frames 0-4, absent in 5-9.
    per_frame = [[_mock_result(1, 0.8)] for _ in range(5)] + [[] for _ in range(5)]

    pipeline = MagicMock()
    pipeline.process_frame.side_effect = per_frame

    votes = attendance_check(frames, pipeline=pipeline, frames_required=3)
    assert len(votes) == 1
    v = votes[0]
    assert v.facestack_person_id == 1
    assert v.frames_seen == 5
    assert v.total_frames == 10
    assert v.is_present is True
    assert abs(v.avg_score - 0.8) < 1e-6


def test_person_seen_in_too_few_frames_is_absent():
    """Bob (id=2) appears in only 2 of 10 frames — below threshold of 3 → ABSENT."""
    frames = [_fake_frame() for _ in range(10)]
    per_frame = [[_mock_result(2, 0.75)] for _ in range(2)] + [[] for _ in range(8)]

    pipeline = MagicMock()
    pipeline.process_frame.side_effect = per_frame

    votes = attendance_check(frames, pipeline=pipeline, frames_required=3)
    assert len(votes) == 1
    assert votes[0].is_present is False
    assert votes[0].frames_seen == 2


def test_score_below_threshold_is_ignored():
    """Carol's scores are all 0.4 — below the 0.6 threshold, so she's never counted."""
    frames = [_fake_frame() for _ in range(10)]
    per_frame = [[_mock_result(3, 0.4)] for _ in range(10)]

    pipeline = MagicMock()
    pipeline.process_frame.side_effect = per_frame

    votes = attendance_check(frames, pipeline=pipeline, score_threshold=0.6)
    assert votes == []  # nobody appeared with a qualifying score


def test_roster_filter_marks_missing_students_absent():
    """Roster has ids {1, 2, 3}. Only Alice (1) appears. Bob and Carol are marked absent."""
    frames = [_fake_frame() for _ in range(10)]
    per_frame = [[_mock_result(1, 0.8)] for _ in range(6)] + [[] for _ in range(4)]

    pipeline = MagicMock()
    pipeline.process_frame.side_effect = per_frame

    votes = attendance_check(
        frames, roster_facestack_ids={1, 2, 3}, pipeline=pipeline, frames_required=3
    )
    by_id = {v.facestack_person_id: v for v in votes}
    assert by_id[1].is_present is True
    assert by_id[2].is_present is False
    assert by_id[2].frames_seen == 0
    assert by_id[3].is_present is False
    assert by_id[3].frames_seen == 0


def test_multiple_people_in_same_frame():
    """Alice and Bob both visible in every frame → both PRESENT."""
    frames = [_fake_frame() for _ in range(10)]
    per_frame = [[_mock_result(1, 0.85), _mock_result(2, 0.78)] for _ in range(10)]

    pipeline = MagicMock()
    pipeline.process_frame.side_effect = per_frame

    votes = attendance_check(frames, pipeline=pipeline, frames_required=3)
    by_id = {v.facestack_person_id: v for v in votes}
    assert by_id[1].is_present is True and by_id[1].frames_seen == 10
    assert by_id[2].is_present is True and by_id[2].frames_seen == 10


def test_empty_frames_returns_empty():
    assert attendance_check([], pipeline=MagicMock()) == []
