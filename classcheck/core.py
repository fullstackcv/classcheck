"""Core attendance logic: vote across a burst of frames."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np

from facestack.config import FaceStackConfig
from facestack.pipeline import FaceStackPipeline

# --- Voting defaults (tune later per deployment) ---
BURST_FRAMES_DEFAULT = 10
FRAMES_REQUIRED_DEFAULT = 3
SCORE_THRESHOLD_DEFAULT = 0.6


@dataclass
class VoteResult:
    """Per-person voting outcome across a burst of frames."""

    facestack_person_id: int
    frames_seen: int
    total_frames: int
    avg_score: float
    is_present: bool


def attendance_check(
    frames: list[np.ndarray],
    roster_facestack_ids: Optional[set[int]] = None,
    pipeline: Optional[FaceStackPipeline] = None,
    frames_required: int = FRAMES_REQUIRED_DEFAULT,
    score_threshold: float = SCORE_THRESHOLD_DEFAULT,
    on_frame=None,
) -> list[VoteResult]:
    """Run the pipeline on each frame, vote per person, return presence.

    Args:
        frames: list of BGR images (np.ndarray) captured in a burst.
        roster_facestack_ids: optional set of facestack person_ids to restrict to.
            If None, every recognized person is returned.
        pipeline: a pre-built FaceStackPipeline. Built from defaults if None.
        frames_required: minimum frames a person must appear in to count as present.
        score_threshold: minimum recognition score for a frame to count.
        on_frame: optional callback `(idx, frame, results) -> None` invoked once
            per frame with the raw pipeline output. Exceptions inside the
            callback are swallowed — it's a non-critical side-channel used
            by callers (e.g. scheduler) to pick a thumbnail.

    Returns:
        One VoteResult per person who was seen in at least one frame.
    """
    if not frames:
        return []

    if pipeline is None:
        pipeline = FaceStackPipeline(config=FaceStackConfig())

    total = len(frames)
    per_person_frames: dict[int, int] = {}      # person_id → frames_seen
    per_person_scores: dict[int, list[float]] = {}  # person_id → list of scores

    for idx, frame in enumerate(frames):
        results = pipeline.process_frame(frame)
        if on_frame is not None:
            try:
                on_frame(idx, frame, results)
            except Exception:
                pass
        # Collect unique person_ids seen in THIS frame (one face per person per frame)
        seen_in_this_frame: dict[int, float] = {}
        for r in results:
            if r.person_id is None:
                continue
            if r.recognition_score < score_threshold:
                continue
            # If the same person shows up multiple times in one frame (shouldn't),
            # keep the highest score.
            prev = seen_in_this_frame.get(r.person_id, -1.0)
            if r.recognition_score > prev:
                seen_in_this_frame[r.person_id] = r.recognition_score

        for pid, score in seen_in_this_frame.items():
            per_person_frames[pid] = per_person_frames.get(pid, 0) + 1
            per_person_scores.setdefault(pid, []).append(score)

    # Build results
    votes: list[VoteResult] = []
    for pid, frames_seen in per_person_frames.items():
        if roster_facestack_ids is not None and pid not in roster_facestack_ids:
            continue
        scores = per_person_scores[pid]
        avg = float(np.mean(scores))
        votes.append(
            VoteResult(
                facestack_person_id=pid,
                frames_seen=frames_seen,
                total_frames=total,
                avg_score=avg,
                is_present=(frames_seen >= frames_required),
            )
        )

    # Also include roster members who were NEVER seen — they're absent.
    if roster_facestack_ids is not None:
        seen_pids = {v.facestack_person_id for v in votes}
        for pid in roster_facestack_ids - seen_pids:
            votes.append(
                VoteResult(
                    facestack_person_id=pid,
                    frames_seen=0,
                    total_frames=total,
                    avg_score=0.0,
                    is_present=False,
                )
            )

    return votes


def capture_burst(
    camera_source: str = "0",
    n_frames: int = BURST_FRAMES_DEFAULT,
    duration_s: float = 30.0,
    hard_timeout_s: float | None = None,
    show_preview: bool = False,
    warmup_s: float = 2.0,
    pipeline=None,
) -> list[np.ndarray]:
    """Grab n_frames evenly spaced over duration_s from the given camera.

    Hardened:
      - Will not run longer than `hard_timeout_s` (defaults to 2x duration_s).
      - Camera buffer set to 1 to avoid stale frames on RTSP/IP cameras.
      - Camera released on any exception path.
      - If `cap.read()` returns failure, we skip that frame and continue —
        we never block forever waiting on a broken camera.
      - With `show_preview=True`, opens a live cv2 window showing every
        frame the camera produces during the burst, with a progress label
        and a brief "CAPTURED" flash when a frame is kept. Press 'q' to
        abort early.

    Args:
        camera_source: "0" for laptop webcam, or an RTSP URL.
        n_frames: how many frames to capture.
        duration_s: spread the capture over this many seconds.
        hard_timeout_s: absolute wall-clock cap on the whole burst.
        show_preview: if True, show a live preview window (debug CLIs only —
                      scheduler daemons should leave this off).

    Returns:
        List of BGR frames (possibly fewer than n_frames if reads failed
        or the hard timeout was hit).
    """
    import logging
    import time as _time

    import cv2

    log = logging.getLogger("classcheck.core.capture")
    hard_timeout = hard_timeout_s if hard_timeout_s is not None else duration_s * 2 + 5.0

    src: object = int(camera_source) if camera_source.isdigit() else camera_source
    cap = cv2.VideoCapture(src)
    try:
        if not cap.isOpened():
            raise RuntimeError(f"Could not open camera source: {camera_source}")

        # For RTSP / IP cameras this avoids reading several-second-old frames.
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass  # Not supported on every backend.

        # Camera warmup: macOS AVFoundation (and many RTSP cameras) return
        # pitch-black frames for ~0.5-2s while auto-exposure / white-balance
        # settle. Read-and-discard until we've either (a) seen a non-black
        # frame or (b) burned `warmup_s` seconds.
        _warmup_start = _time.monotonic()
        while warmup_s > 0 and _time.monotonic() - _warmup_start < warmup_s:
            ok, frame = cap.read()
            if ok and frame is not None and float(frame.mean()) > 15.0:
                # mean pixel value > 15 means the image has real content
                # (pure black is 0; a typical dim indoor frame is ~40-80).
                break
            if show_preview and ok and frame is not None:
                # Let the user see warmup progress so the window isn't frozen.
                import cv2 as _cv2
                _cv2.putText(
                    frame, "Warming up camera...", (20, 40),
                    _cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2,
                )
                _cv2.imshow("ClassCheck - Burst Capture (q to abort)", frame)
                _cv2.waitKey(1)

        frames: list[np.ndarray] = []

        t0 = _time.monotonic()
        deadline = t0 + hard_timeout
        # Target times at which we want to *keep* a frame. Evenly spaced.
        target_times = [
            t0 + i * (duration_s / max(1, n_frames - 1))
            for i in range(n_frames)
        ] if n_frames > 1 else [t0]

        next_target_idx = 0
        last_captured_mono = -1e9

        # Stream the camera continuously; capture frames whose time has come.
        while next_target_idx < n_frames:
            now = _time.monotonic()
            if now >= deadline:
                log.warning(
                    "capture_burst hit hard_timeout=%.1fs after %d/%d frames",
                    hard_timeout, len(frames), n_frames,
                )
                break

            ok, frame = cap.read()
            if not ok or frame is None:
                log.debug("capture_burst: cap.read() returned failure")
                if not show_preview:
                    # Don't busy-loop the CPU if reads are failing without a
                    # display to refresh anyway.
                    _time.sleep(0.02)
                continue

            # Is THIS frame our next scheduled capture?
            if now >= target_times[next_target_idx]:
                frames.append(frame)
                last_captured_mono = now
                next_target_idx += 1

            if show_preview:
                preview = frame.copy()

                # Run the full pipeline on this preview frame so the user
                # can see who's being detected + recognized in real time.
                # Failures here should NEVER break the capture — this is
                # just a UI annotation.
                if pipeline is not None:
                    try:
                        results = pipeline.process_frame(frame)
                    except Exception:
                        results = []
                    for r in results:
                        x1, y1, x2, y2 = r.bbox
                        if r.person_name:
                            label = f"{r.person_name} {r.recognition_score:.2f}"
                            box_color = (0, 255, 0)       # green — recognized
                        elif r.person_id is not None:
                            label = f"ID:{r.person_id} {r.recognition_score:.2f}"
                            box_color = (0, 255, 0)
                        else:
                            label = f"Unknown (best:{r.recognition_score:.2f})"
                            box_color = (0, 165, 255)     # orange — detected-but-unknown
                        cv2.rectangle(preview, (x1, y1), (x2, y2), box_color, 2)
                        cv2.putText(
                            preview, label, (x1, max(20, y1 - 8)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, box_color, 2,
                        )

                elapsed = now - t0
                remaining = max(0.0, duration_s - elapsed)
                captured_flash = (now - last_captured_mono) < 0.25
                status_color = (0, 255, 0) if captured_flash else (0, 200, 255)
                label = f"Burst {len(frames)}/{n_frames}   {remaining:.1f}s left"
                cv2.putText(
                    preview, label, (20, 40),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, status_color, 2,
                )
                if captured_flash:
                    cv2.putText(
                        preview, "CAPTURED", (20, 80),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3,
                    )
                cv2.imshow("ClassCheck - Burst Capture (q to abort)", preview)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    log.info("capture_burst: user pressed q, aborting at %d/%d",
                             len(frames), n_frames)
                    break

        return frames
    finally:
        cap.release()
        if show_preview:
            try:
                import cv2 as _cv2
                _cv2.destroyWindow("ClassCheck - Burst Capture (q to abort)")
            except Exception:
                pass
