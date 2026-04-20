"""ClassCheck admin CLI — create rooms, schedules, and enroll students.

Usage:
    classcheck-admin create-room --name "Room 101" --camera "0"
    classcheck-admin add-schedule --room "Room 101" --time 11:00 --class "Math-10A" --teacher "Mr Sharma"
    classcheck-admin enroll --person "Alice" --class "Math-10A"
    classcheck-admin list-rooms
    classcheck-admin list-schedules
    classcheck-admin list-persons
"""

import argparse
import sys
from datetime import time
from typing import Optional

from classcheck.models import (
    Enrollment,
    Person,
    Room,
    Schedule,
    get_session,
    init_db,
)


def _parse_time(s: str) -> time:
    parts = s.split(":")
    if len(parts) != 2:
        raise argparse.ArgumentTypeError(f"Time must be HH:MM, got {s!r}")
    return time(hour=int(parts[0]), minute=int(parts[1]))


def _find_person(session, name: str) -> Optional[Person]:
    return session.query(Person).filter(Person.name == name).first()


def _find_room(session, name: str) -> Optional[Room]:
    return session.query(Room).filter(Room.name == name).first()


def _find_schedule_by_label(session, class_label: str) -> Optional[Schedule]:
    return session.query(Schedule).filter(Schedule.class_label == class_label).first()


# --- Commands ---

def cmd_create_room(args, session) -> None:
    if _find_room(session, args.name):
        print(f"Room {args.name!r} already exists.")
        return
    r = Room(name=args.name, camera_url=args.camera)
    session.add(r)
    session.commit()
    print(f"Created room: id={r.id} name={r.name!r} camera={r.camera_url!r}")


def cmd_add_schedule(args, session) -> None:
    room = _find_room(session, args.room)
    if room is None:
        print(f"Room {args.room!r} not found. Create it first.", file=sys.stderr)
        sys.exit(1)

    teacher_id = None
    if args.teacher:
        teacher = _find_person(session, args.teacher)
        if teacher is None:
            print(
                f"Teacher {args.teacher!r} not found. Run `classcheck-enroll --role teacher` first.",
                file=sys.stderr,
            )
            sys.exit(1)
        if teacher.role != "teacher":
            print(f"Warning: {args.teacher!r} has role={teacher.role!r}, not teacher.")
        teacher_id = teacher.id

    sched = Schedule(
        room_id=room.id,
        time_of_day=args.time,
        class_label=args.class_label,
        teacher_id=teacher_id,
    )
    session.add(sched)
    session.commit()
    print(
        f"Added schedule: id={sched.id} room={room.name!r} "
        f"time={sched.time_of_day} class={sched.class_label!r} teacher_id={teacher_id}"
    )


def cmd_enroll(args, session) -> None:
    person = _find_person(session, args.person)
    if person is None:
        print(f"Person {args.person!r} not found. Enroll them via classcheck-enroll first.")
        sys.exit(1)
    sched = _find_schedule_by_label(session, args.class_label)
    if sched is None:
        print(f"Class {args.class_label!r} not found.")
        sys.exit(1)

    existing = (
        session.query(Enrollment)
        .filter(Enrollment.person_id == person.id, Enrollment.schedule_id == sched.id)
        .first()
    )
    if existing:
        print(f"Already enrolled.")
        return

    e = Enrollment(person_id=person.id, schedule_id=sched.id)
    session.add(e)
    session.commit()
    print(f"Enrolled {person.name!r} ({person.role}) in class {sched.class_label!r}.")


def cmd_list_rooms(args, session) -> None:
    rows = session.query(Room).all()
    if not rows:
        print("(no rooms)")
        return
    for r in rows:
        print(f"  [{r.id}] {r.name!r} — camera={r.camera_url!r}")


def cmd_list_schedules(args, session) -> None:
    rows = session.query(Schedule).all()
    if not rows:
        print("(no schedules)")
        return
    for s in rows:
        teacher = s.teacher.name if s.teacher else "-"
        n_students = len(s.enrollments)
        print(
            f"  [{s.id}] {s.room.name!r} @ {s.time_of_day} — "
            f"class={s.class_label!r} teacher={teacher!r} students={n_students}"
        )


def cmd_fire_now(args, session) -> None:
    """Trigger run_sampling immediately against a specific schedule.

    Useful for testing without waiting for cron to fire.
    """
    import logging as _logging

    # Configure logging so run_sampling's INFO messages reach the console.
    _logging.basicConfig(
        level=_logging.INFO, format="%(levelname)s %(name)s: %(message)s", force=True,
    )

    # Import here so classcheck-admin doesn't pull facestack for non-fire commands.
    from classcheck.models import Observation, Snapshot
    from classcheck.scheduler import run_sampling

    sched = session.query(Schedule).filter(Schedule.id == args.schedule).first()
    if sched is None:
        print(f"Schedule id={args.schedule} not found.", file=sys.stderr)
        sys.exit(1)

    session.close()  # run_sampling opens its own session

    # Debug path: show the live camera window so the user can see the burst.
    snap_id = run_sampling(args.schedule, args.db, show_preview=True)
    if snap_id is None:
        print("No snapshot written (camera failed or no frames).")
        sys.exit(1)

    # Re-open a session and print the per-person result
    session2 = get_session(args.db)
    try:
        snap = session2.query(Snapshot).filter(Snapshot.id == snap_id).one()
        obs = (
            session2.query(Observation)
            .filter(Observation.snapshot_id == snap_id)
            .all()
        )
        print()
        print(f"Snapshot {snap_id}  {snap.actual_time.strftime('%H:%M:%S')}  "
              f"({snap.n_frames} frames)")
        print(f"{'NAME':<20} {'ROLE':<10} {'SEEN':<10} {'SCORE':<8} {'STATUS'}")
        print("-" * 60)
        for o in obs:
            print(
                f"{o.person.name:<20} {o.person.role:<10} "
                f"{o.frames_seen}/{o.total_frames:<7} "
                f"{o.avg_score:<8.2f} "
                f"{'PRESENT' if o.is_present else 'absent'}"
            )
        present = sum(1 for o in obs if o.is_present)
        print(f"\n{present} / {len(obs)} present.")
    finally:
        session2.close()


def cmd_list_persons(args, session) -> None:
    rows = session.query(Person).all()
    if not rows:
        print("(no persons)")
        return
    for p in rows:
        print(
            f"  [{p.id}] {p.name!r} role={p.role!r} "
            f"facestack_person_id={p.facestack_person_id}"
        )


# --- Entry point ---

def main() -> None:
    parser = argparse.ArgumentParser(description="ClassCheck admin")
    parser.add_argument("--db", default=None, help="DB URL (default: ~/.classcheck/classcheck.db)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("create-room", help="Add a classroom + camera")
    p.add_argument("--name", required=True)
    p.add_argument("--camera", required=True, help='Camera source ("0" or RTSP URL)')
    p.set_defaults(func=cmd_create_room)

    p = sub.add_parser("add-schedule", help="Add a time slot to a room")
    p.add_argument("--room", required=True, help="Room name")
    p.add_argument("--time", type=_parse_time, required=True, help="HH:MM")
    p.add_argument("--class", dest="class_label", required=True, help="Class label, e.g. Math-10A")
    p.add_argument("--teacher", default=None, help="Name of an enrolled teacher (optional)")
    p.set_defaults(func=cmd_add_schedule)

    p = sub.add_parser("enroll", help="Enroll a student in a class")
    p.add_argument("--person", required=True)
    p.add_argument("--class", dest="class_label", required=True)
    p.set_defaults(func=cmd_enroll)

    p = sub.add_parser("list-rooms")
    p.set_defaults(func=cmd_list_rooms)

    p = sub.add_parser("list-schedules")
    p.set_defaults(func=cmd_list_schedules)

    p = sub.add_parser("list-persons")
    p.set_defaults(func=cmd_list_persons)

    p = sub.add_parser("fire-now", help="Run sampling immediately for one schedule")
    p.add_argument("--schedule", type=int, required=True, help="Schedule id (see list-schedules)")
    p.set_defaults(func=cmd_fire_now)

    args = parser.parse_args()
    init_db(args.db)
    session = get_session(args.db)
    try:
        args.func(args, session)
    finally:
        session.close()


if __name__ == "__main__":
    main()
