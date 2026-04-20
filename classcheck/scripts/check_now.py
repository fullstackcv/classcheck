"""Trigger a single attendance check RIGHT NOW against a camera.

Captures a burst of frames, runs facestack recognition on each, votes, and
prints which enrolled people are present / absent. Does NOT write to the
database — this is a diagnostic / smoke-test command.

Usage:
    classcheck-check                          # defaults: 10 frames over 30s, laptop webcam
    classcheck-check --frames 5 --duration 10 # quicker test
    classcheck-check --no-roster              # show EVERY enrolled person (no filter)
"""

import argparse
import logging
from typing import Optional

from facestack.pipeline import FaceStackPipeline

from classcheck.core import attendance_check, capture_burst
from classcheck.models import Person, Role, get_session, init_db
from classcheck.paths import build_facestack_config

logger = logging.getLogger("classcheck.check")


def main(argv: Optional[list] = None) -> None:
    parser = argparse.ArgumentParser(description="One-shot attendance check from a live camera.")
    parser.add_argument("--camera", default="0", help='Camera source (default "0")')
    parser.add_argument("--frames", type=int, default=10, help="Frames to capture in the burst")
    parser.add_argument("--duration", type=float, default=30.0, help="Burst duration in seconds")
    parser.add_argument("--threshold", type=int, default=3, help="Frames required to mark PRESENT")
    parser.add_argument("--score", type=float, default=0.6, help="Min recognition score per frame")
    parser.add_argument(
        "--no-roster",
        action="store_true",
        help="Don't filter by the ClassCheck Person table — show anyone facestack recognizes",
    )
    parser.add_argument("--db", default=None, help="ClassCheck DB URL (default: ~/.classcheck/classcheck.db)")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level), format="%(levelname)s %(message)s")

    # Load roster from ClassCheck DB (or skip if --no-roster)
    roster_ids = None
    id_to_person: dict[int, Person] = {}
    if not args.no_roster:
        init_db(args.db)
        session = get_session(args.db)
        try:
            persons = session.query(Person).all()
            if not persons:
                print("No persons enrolled yet. Run `classcheck-enroll` first, or use --no-roster.")
                return
            roster_ids = {p.facestack_person_id for p in persons if p.facestack_person_id}
            id_to_person = {p.facestack_person_id: p for p in persons if p.facestack_person_id}
        finally:
            session.close()

    # Build pipeline first so the preview window can annotate detections live.
    print("Loading pipeline...")
    pipeline = FaceStackPipeline(config=build_facestack_config())
    try:
        print(f"Capturing {args.frames} frames over {args.duration:.0f}s from camera {args.camera}...")
        frames = capture_burst(
            camera_source=args.camera,
            n_frames=args.frames,
            duration_s=args.duration,
            show_preview=True,   # debug CLI — always show the live camera
            pipeline=pipeline,    # ...with detection + recognition overlays
        )
        if not frames:
            print("No frames captured. Check the camera.")
            return
        print(f"Got {len(frames)} frames. Running recognition...")

        votes = attendance_check(
            frames,
            roster_facestack_ids=roster_ids,
            pipeline=pipeline,
            frames_required=args.threshold,
            score_threshold=args.score,
        )
    finally:
        pipeline.close()

    # Display
    print()
    print(f"{'NAME':<25} {'ROLE':<10} {'SEEN':<10} {'AVG SCORE':<12} {'STATUS'}")
    print("-" * 70)
    # Sort: present first (by frames_seen desc), then absent (by name)
    votes.sort(key=lambda v: (-int(v.is_present), -v.frames_seen))
    for v in votes:
        p = id_to_person.get(v.facestack_person_id)
        name = p.name if p else f"(facestack id {v.facestack_person_id})"
        role = p.role if p else "-"
        status = "PRESENT" if v.is_present else "absent"
        print(
            f"{name:<25} {role:<10} {v.frames_seen}/{v.total_frames:<7} "
            f"{v.avg_score:<12.2f} {status}"
        )

    present_count = sum(1 for v in votes if v.is_present)
    print(f"\n{present_count} present out of {len(votes)} in roster.")


if __name__ == "__main__":
    main()
