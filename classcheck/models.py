"""SQLAlchemy models for ClassCheck.

ClassCheck owns its own database. Students and teachers are linked back to
facestack's embedded Person table via `facestack_person_id` — facestack
handles face embeddings + recognition; ClassCheck handles rosters, schedules,
and presence observations.
"""

from datetime import date, datetime, time
from enum import Enum
from typing import Optional

from sqlalchemy import (
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Time,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


class Role(str, Enum):
    """What kind of person this is. Drives how their attendance is reported."""

    STUDENT = "student"
    TEACHER = "teacher"
    ADMIN = "admin"


class Base(DeclarativeBase):
    pass


class Person(Base):
    __tablename__ = "persons"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))  # values from Role enum
    email: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)
    facestack_person_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, unique=True, index=True
    )

    # Reverse relationships
    enrollments: Mapped[list["Enrollment"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )
    observations: Mapped[list["Observation"]] = relationship(
        back_populates="person", cascade="all, delete-orphan"
    )
    teaches: Mapped[list["Schedule"]] = relationship(
        back_populates="teacher", foreign_keys="Schedule.teacher_id"
    )

    def __repr__(self) -> str:
        return f"Person(id={self.id}, name={self.name!r}, role={self.role!r})"


class Room(Base):
    __tablename__ = "rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    camera_url: Mapped[str] = mapped_column(String(500))
    # camera_url: "0" for laptop webcam, or "rtsp://..." for IP camera

    schedules: Mapped[list["Schedule"]] = relationship(
        back_populates="room", cascade="all, delete-orphan"
    )


class Schedule(Base):
    __tablename__ = "schedules"

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("rooms.id"), index=True)
    time_of_day: Mapped[time] = mapped_column(Time)  # e.g. time(11, 0) for 11:00 AM
    class_label: Mapped[str] = mapped_column(String(100))  # e.g. "Math-10A"
    teacher_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("persons.id"), nullable=True, index=True
    )

    room: Mapped["Room"] = relationship(back_populates="schedules")
    teacher: Mapped[Optional["Person"]] = relationship(
        back_populates="teaches", foreign_keys=[teacher_id]
    )
    enrollments: Mapped[list["Enrollment"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )
    snapshots: Mapped[list["Snapshot"]] = relationship(
        back_populates="schedule", cascade="all, delete-orphan"
    )


class Enrollment(Base):
    """Which students are expected in which schedule slot."""

    __tablename__ = "enrollments"

    id: Mapped[int] = mapped_column(primary_key=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), index=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id"), index=True)

    __table_args__ = (
        UniqueConstraint("person_id", "schedule_id", name="uq_enrollment_person_schedule"),
    )

    person: Mapped["Person"] = relationship(back_populates="enrollments")
    schedule: Mapped["Schedule"] = relationship(back_populates="enrollments")


class Snapshot(Base):
    """One sampling event — a burst of N frames taken at a scheduled time."""

    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    schedule_id: Mapped[int] = mapped_column(ForeignKey("schedules.id"), index=True)
    scheduled_date: Mapped[date] = mapped_column(Date, index=True)
    scheduled_time: Mapped[time] = mapped_column(Time)
    actual_time: Mapped[datetime] = mapped_column(DateTime)
    n_frames: Mapped[int] = mapped_column(Integer)
    thumbnail_path: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)

    schedule: Mapped["Schedule"] = relationship(back_populates="snapshots")
    observations: Mapped[list["Observation"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class Observation(Base):
    """Voting result for one person in one snapshot."""

    __tablename__ = "observations"

    id: Mapped[int] = mapped_column(primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"), index=True)
    person_id: Mapped[int] = mapped_column(ForeignKey("persons.id"), index=True)
    frames_seen: Mapped[int] = mapped_column(Integer)
    total_frames: Mapped[int] = mapped_column(Integer)
    avg_score: Mapped[float] = mapped_column(Float)
    is_present: Mapped[bool] = mapped_column()

    snapshot: Mapped["Snapshot"] = relationship(back_populates="observations")
    person: Mapped["Person"] = relationship(back_populates="observations")

    __table_args__ = (
        UniqueConstraint("snapshot_id", "person_id", name="uq_observation_snapshot_person"),
    )


# --- Engine & session helpers ---

def _default_url() -> str:
    # Imported lazily so that importing models doesn't create ~/.classcheck.
    from classcheck.paths import db_url as _db_url
    return _db_url()


def get_engine(database_url: Optional[str] = None):
    """Return a SQLAlchemy engine for the ClassCheck database."""
    if database_url is None:
        database_url = _default_url()
    return create_engine(database_url, future=True)


def init_db(database_url: Optional[str] = None) -> None:
    """Create all tables if they don't exist, and apply tiny ad-hoc migrations.

    We don't use Alembic here — this product is small and we can handle the
    one or two forward migrations inline. Every migration below is idempotent
    (NOP if already applied).
    """
    if database_url is None:
        database_url = _default_url()
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)

    # Migrations for DBs created before a column was added (all idempotent).
    with engine.begin() as conn:
        from sqlalchemy import inspect, text

        inspector = inspect(conn)
        if "persons" in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns("persons")}
            if "email" not in cols:
                conn.execute(text("ALTER TABLE persons ADD COLUMN email VARCHAR(200)"))
        if "snapshots" in inspector.get_table_names():
            cols = {c["name"] for c in inspector.get_columns("snapshots")}
            if "thumbnail_path" not in cols:
                conn.execute(
                    text("ALTER TABLE snapshots ADD COLUMN thumbnail_path VARCHAR(500)")
                )


def get_session(database_url: Optional[str] = None):
    """Return a new SQLAlchemy session.

    `expire_on_commit=False` means attribute values stay populated after a
    commit, even after the session closes — so callers can read the new row's
    fields without hitting DetachedInstanceError. Trade-off: if the underlying
    row is changed by another process, our in-memory instance won't see it
    until explicitly refreshed. For this product's short-lived sessions that's
    the right default.
    """
    if database_url is None:
        database_url = _default_url()
    engine = get_engine(database_url)
    Session = sessionmaker(bind=engine, future=True, expire_on_commit=False)
    return Session()
