"""APScheduler daemon — fires attendance sampling at each Schedule's time_of_day.

Reads the Schedule table once at startup, installs one cron job per row, then
blocks. Each fired job:
  1. Loads the schedule's roster (enrolled students + expected teacher).
  2. Captures a burst of frames from the room's camera.
  3. Runs attendance_check() to vote presence.
  4. Writes one Snapshot row + one Observation row per roster member.
"""

import argparse
import logging
import signal
import sys
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from facestack.pipeline import FaceStackPipeline

from classcheck.core import attendance_check, capture_burst
from classcheck.models import (
    Observation,
    Schedule,
    Snapshot,
    get_session,
    init_db,
)
from classcheck.paths import build_facestack_config, snapshots_dir

logger = logging.getLogger("classcheck.scheduler")


def run_sampling(
    schedule_id: int,
    db_url: str,
    pipeline: Optional[FaceStackPipeline] = None,
    show_preview: bool = False,
) -> Optional[int]:
    """Fire one sampling cycle for a single Schedule.

    Returns the new Snapshot.id, or None on skip/error.

    Hardened: any exception inside this function is caught, logged, and
    suppressed — a single bad schedule must not crash the daemon.
    """
    now = datetime.now()
    session = get_session(db_url)
    owns_pipeline = False
    try:
        sched = session.query(Schedule).filter(Schedule.id == schedule_id).first()
        if sched is None:
            logger.error("Schedule id=%d not found; skipping", schedule_id)
            return None

        # Roster = enrolled students + expected teacher (if any)
        roster_persons = [e.person for e in sched.enrollments]
        if sched.teacher is not None:
            roster_persons.append(sched.teacher)

        roster_ids = {p.facestack_person_id for p in roster_persons if p.facestack_person_id}
        id_to_cc = {p.facestack_person_id: p for p in roster_persons if p.facestack_person_id}

        room = sched.room
        logger.info(
            "Sampling room=%r schedule_id=%d @ %s (roster=%d)",
            room.name, schedule_id, now.strftime("%H:%M:%S"), len(roster_ids),
        )

        # Build pipeline FIRST so the live preview (when show_preview=True)
        # can annotate camera frames with detection + recognition in real time.
        if pipeline is None:
            pipeline = FaceStackPipeline(config=build_facestack_config())
            owns_pipeline = True

        try:
            frames = capture_burst(
                camera_source=room.camera_url,
                show_preview=show_preview,
                pipeline=pipeline,
            )
        except RuntimeError as e:
            logger.error("Camera failed for schedule_id=%d: %s", schedule_id, e)
            return None
        if not frames:
            logger.warning("No frames captured for schedule_id=%d", schedule_id)
            return None

        # Track the "best" frame for the thumbnail as we process.
        # Best = frame with the highest sum of recognition scores.
        _thumb_state = {"score_sum": -1.0, "annotated": None}

        def _pick_best_frame(idx, frame, results):
            recognized = [r for r in results if r.person_id is not None]
            if not recognized:
                return
            score_sum = float(sum(r.recognition_score for r in recognized))
            if score_sum <= _thumb_state["score_sum"]:
                return
            # Annotate this frame and keep it as the current best.
            # Scale annotation with the frame size so text is readable when
            # the thumbnail is rendered large in the dashboard.
            import cv2 as _cv2
            ann = frame.copy()
            h, w = ann.shape[:2]
            font_scale = max(0.8, min(2.0, w / 640.0))
            thickness = max(2, int(round(font_scale * 2)))
            for r in results:
                x1, y1, x2, y2 = r.bbox
                if r.person_name:
                    lab = f"{r.person_name} {r.recognition_score:.2f}"
                    col = (0, 255, 0)
                elif r.person_id is not None:
                    lab = f"ID:{r.person_id} {r.recognition_score:.2f}"
                    col = (0, 255, 0)
                else:
                    lab = f"Unknown (best:{r.recognition_score:.2f})"
                    col = (0, 165, 255)
                _cv2.rectangle(ann, (x1, y1), (x2, y2), col, thickness + 1)
                # Solid background behind text so the label is legible over
                # any face region.
                (tw, th), _ = _cv2.getTextSize(
                    lab, _cv2.FONT_HERSHEY_SIMPLEX, font_scale, thickness,
                )
                bg_y1 = max(0, y1 - th - 12)
                _cv2.rectangle(ann, (x1, bg_y1), (x1 + tw + 10, y1), col, -1)
                _cv2.putText(
                    ann, lab, (x1 + 5, y1 - 6),
                    _cv2.FONT_HERSHEY_SIMPLEX, font_scale, (0, 0, 0), thickness,
                )
            _thumb_state["score_sum"] = score_sum
            _thumb_state["annotated"] = ann

        try:
            votes = attendance_check(
                frames,
                roster_facestack_ids=roster_ids,
                pipeline=pipeline,
                on_frame=_pick_best_frame,
            )
        except Exception:
            logger.exception("Recognition failed for schedule_id=%d", schedule_id)
            return None

        snap = Snapshot(
            schedule_id=schedule_id,
            scheduled_date=now.date(),
            scheduled_time=sched.time_of_day,
            actual_time=now,
            n_frames=len(frames),
        )
        session.add(snap)
        session.flush()  # populate snap.id

        # Thumbnail priority:
        #   1. Frame with the highest-scoring RECOGNITION (picked during
        #      attendance_check via the callback — already annotated with
        #      bbox + name + score).
        #   2. If no recognition happened anywhere: the first frame with
        #      any detection, unannotated.
        #   3. Else: the brightest frame (skips webcam-warmup black).
        thumb_frame = _thumb_state["annotated"]
        if thumb_frame is None:
            for f in frames:
                try:
                    if pipeline._detector.detect(f):
                        thumb_frame = f
                        break
                except Exception:
                    continue
            if thumb_frame is None:
                brightness = [float(f.mean()) for f in frames]
                ti = brightness.index(max(brightness))
                thumb_frame = frames[ti]
                logger.warning(
                    "No face detected in any of %d burst frames. "
                    "Saved brightest frame (mean=%.1f, idx=%d) as thumbnail.",
                    len(frames), brightness[ti], ti,
                )

        try:
            import cv2 as _cv2
            thumb_dir = snapshots_dir() / now.strftime("%Y-%m-%d")
            thumb_dir.mkdir(parents=True, exist_ok=True)
            thumb_path = thumb_dir / f"snap_{snap.id}.jpg"
            _cv2.imwrite(str(thumb_path), thumb_frame, [_cv2.IMWRITE_JPEG_QUALITY, 80])
            snap.thumbnail_path = str(thumb_path)
        except Exception:
            logger.exception("Failed to save thumbnail for snapshot_id=%d", snap.id)

        for v in votes:
            cc_person = id_to_cc.get(v.facestack_person_id)
            if cc_person is None:
                continue  # recognized but not on this roster — ignore
            session.add(
                Observation(
                    snapshot_id=snap.id,
                    person_id=cc_person.id,
                    frames_seen=v.frames_seen,
                    total_frames=v.total_frames,
                    avg_score=v.avg_score,
                    is_present=v.is_present,
                )
            )
        session.commit()

        present = sum(1 for v in votes if v.is_present)
        logger.info(
            "Snapshot %d written: %d present / %d in roster",
            snap.id, present, len(votes),
        )
        return snap.id
    except Exception:
        # Never let a single failed sampling crash the APScheduler daemon.
        logger.exception("Unhandled error in run_sampling(schedule_id=%d)", schedule_id)
        try:
            session.rollback()
        except Exception:
            pass
        return None
    finally:
        session.close()
        if owns_pipeline and pipeline is not None:
            try:
                pipeline.close()
            except Exception:
                pass


def build_scheduler(db_url: str) -> BlockingScheduler:
    """Read the Schedule table and register one cron job per row."""
    sched = BlockingScheduler()
    session = get_session(db_url)
    try:
        rows = session.query(Schedule).all()
        for s in rows:
            trigger = CronTrigger(hour=s.time_of_day.hour, minute=s.time_of_day.minute)
            sched.add_job(
                run_sampling,
                trigger,
                args=[s.id, db_url],
                id=f"sample-{s.id}",
                replace_existing=True,
                max_instances=1,
                coalesce=True,
            )
            logger.info(
                "Registered: schedule_id=%d at %02d:%02d (room=%r, class=%r)",
                s.id, s.time_of_day.hour, s.time_of_day.minute,
                s.room.name, s.class_label,
            )
    finally:
        session.close()
    return sched


def main() -> None:
    parser = argparse.ArgumentParser(description="ClassCheck sampling scheduler daemon.")
    parser.add_argument("--db", default=None, help="DB URL (default: ~/.classcheck/classcheck.db)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    init_db(args.db)
    sched = build_scheduler(args.db)
    jobs = sched.get_jobs()
    if not jobs:
        print("No schedules in the DB. Run `classcheck-admin add-schedule ...` first.")
        return

    print(f"ClassCheck scheduler running — {len(jobs)} jobs. Ctrl+C to stop.")
    for j in jobs:
        # APScheduler API varies across 3.x / 4.x. Fall back gracefully.
        nxt = getattr(j, "next_run_time", None)
        trig = getattr(j, "trigger", "?")
        print(f"  • {j.id}: next={nxt}  trigger={trig}")

    # Graceful shutdown on SIGTERM (Docker stop, systemd, kill).
    # SIGINT (Ctrl+C) is handled by BlockingScheduler's own KeyboardInterrupt path.
    def _sigterm_handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("Received %s, shutting down scheduler...", sig_name)
        try:
            sched.shutdown(wait=False)
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _sigterm_handler)

    try:
        sched.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutting down scheduler (Ctrl+C)...")
        try:
            sched.shutdown(wait=False)
        except Exception:
            pass


if __name__ == "__main__":
    main()
