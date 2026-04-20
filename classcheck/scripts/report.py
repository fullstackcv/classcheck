"""classcheck-report CLI — generate daily/monthly attendance CSVs.

Usage:
    classcheck-report student-roll --class "Math-10A" --date 2026-04-17
    classcheck-report student-roll --class "Math-10A" --date today -o roll.csv
    classcheck-report teacher-day  --date today
    classcheck-report monthly      --class "Math-10A" --year 2026 --month 4
"""

import argparse
import sys

from classcheck.emailer import EmailConfigError, send_csv_report
from classcheck.models import Person, Role, Schedule, get_session, init_db
from classcheck.reports import (
    daily_student_roll,
    daily_teacher_attendance,
    monthly_summary,
    parse_date,
)


def _write_or_print(csv_text: str, output_path: str | None) -> None:
    if output_path:
        with open(output_path, "w") as f:
            f.write(csv_text)
        print(f"Wrote {output_path}")
    else:
        sys.stdout.write(csv_text)


def cmd_student_roll(args, session) -> None:
    on_date = parse_date(args.date)
    try:
        csv_text = daily_student_roll(session, args.class_label, on_date)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    _write_or_print(csv_text, args.output)


def cmd_teacher_day(args, session) -> None:
    on_date = parse_date(args.date)
    csv_text = daily_teacher_attendance(session, on_date)
    _write_or_print(csv_text, args.output)


def cmd_monthly(args, session) -> None:
    try:
        csv_text = monthly_summary(session, args.class_label, args.year, args.month)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    _write_or_print(csv_text, args.output)


def cmd_send(args, session) -> None:
    """Generate a report and email it to the right people based on its kind.

    Routing:
      student-roll → teacher of the class (must have an email)
      teacher-day  → every Person with role=admin who has an email
      monthly      → teacher + all admins
    """
    kind = args.kind

    # Resolve the CSV + the default recipients based on kind.
    recipients: list[str] = []
    if kind in ("student-roll", "monthly"):
        sched = session.query(Schedule).filter(Schedule.class_label == args.class_label).first()
        if sched is None:
            print(f"Class {args.class_label!r} not found.", file=sys.stderr)
            sys.exit(1)
        if sched.teacher and sched.teacher.email:
            recipients.append(sched.teacher.email)

    if kind in ("teacher-day", "monthly"):
        admins = (
            session.query(Person)
            .filter(Person.role == Role.ADMIN.value, Person.email.isnot(None))
            .all()
        )
        recipients.extend(a.email for a in admins)

    # Explicit --to overrides routing
    if args.to:
        recipients = list(args.to)

    if not recipients:
        print(
            "No recipients resolved. Either pass --to, or give the teacher/admin "
            "an email (re-enroll with --email, or update the DB).",
            file=sys.stderr,
        )
        sys.exit(1)

    # Generate the CSV
    if kind == "student-roll":
        on_date = parse_date(args.date)
        csv_text = daily_student_roll(session, args.class_label, on_date)
        fname = f"roll_{args.class_label}_{on_date}.csv"
        subject = f"Student roll — {args.class_label} — {on_date}"
    elif kind == "teacher-day":
        on_date = parse_date(args.date)
        csv_text = daily_teacher_attendance(session, on_date)
        fname = f"teachers_{on_date}.csv"
        subject = f"Teacher attendance — {on_date}"
    elif kind == "monthly":
        csv_text = monthly_summary(session, args.class_label, args.year, args.month)
        fname = f"monthly_{args.class_label}_{args.year}-{args.month:02d}.csv"
        subject = f"Monthly attendance — {args.class_label} — {args.year}-{args.month:02d}"
    else:
        print(f"Unknown kind: {kind!r}", file=sys.stderr)
        sys.exit(1)

    try:
        send_csv_report(
            to=recipients,
            subject=subject,
            body=f"Automated report from ClassCheck.\n\nSee attached: {fname}\n",
            csv_text=csv_text,
            csv_filename=fname,
        )
    except EmailConfigError as e:
        print(f"Email config error: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"Sent {fname} to {', '.join(recipients)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate ClassCheck attendance reports.")
    parser.add_argument("--db", default=None, help="DB URL (default: ~/.classcheck/classcheck.db)")
    parser.add_argument("-o", "--output", default=None, help="Write CSV to file (default: stdout)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("student-roll", help="Daily student roll for one class")
    p.add_argument("--class", dest="class_label", required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD, 'today', or 'yesterday'")
    p.set_defaults(func=cmd_student_roll)

    p = sub.add_parser("teacher-day", help="All teachers' presence for a date")
    p.add_argument("--date", required=True, help="YYYY-MM-DD, 'today', or 'yesterday'")
    p.set_defaults(func=cmd_teacher_day)

    p = sub.add_parser("monthly", help="Per-student monthly aggregate for one class")
    p.add_argument("--class", dest="class_label", required=True)
    p.add_argument("--year", type=int, required=True)
    p.add_argument("--month", type=int, required=True, help="1-12")
    p.set_defaults(func=cmd_monthly)

    p = sub.add_parser("send", help="Generate a report AND email it to recipients")
    p.add_argument(
        "kind",
        choices=["student-roll", "teacher-day", "monthly"],
        help="Which report to generate",
    )
    p.add_argument("--class", dest="class_label", default=None, help="Required for student-roll / monthly")
    p.add_argument("--date", default=None, help="YYYY-MM-DD, 'today', 'yesterday' (for student-roll / teacher-day)")
    p.add_argument("--year", type=int, default=None, help="For monthly")
    p.add_argument("--month", type=int, default=None, help="For monthly (1-12)")
    p.add_argument(
        "--to",
        nargs="+",
        default=None,
        help="Override recipients. Defaults: teacher (student-roll), admins (teacher-day), both (monthly).",
    )
    p.set_defaults(func=cmd_send)

    args = parser.parse_args()
    init_db(args.db)
    session = get_session(args.db)
    try:
        args.func(args, session)
    finally:
        session.close()


if __name__ == "__main__":
    main()
