"""Where ClassCheck keeps its files on disk.

CWD-independent — no matter where you run a command from, everything
lands under `~/.classcheck/`. Override with CLASSCHECK_HOME env var.

Contents:
    ~/.classcheck/
    ├── classcheck.db        ClassCheck's own DB (rooms, schedules, observations)
    ├── facestack.db         Facestack's DB (enrolled persons + embeddings)
    └── snapshots/
        └── YYYY-MM-DD/
            └── snap_<id>.jpg
"""

import os
from pathlib import Path


def data_dir() -> Path:
    """The per-user classcheck data directory. Created if missing."""
    root = os.environ.get("CLASSCHECK_HOME")
    d = Path(root).expanduser() if root else Path.home() / ".classcheck"
    d.mkdir(parents=True, exist_ok=True)
    return d


def db_path() -> Path:
    return data_dir() / "classcheck.db"


def db_url() -> str:
    """SQLAlchemy URL for ClassCheck's own DB."""
    return f"sqlite:///{db_path()}"


def facestack_db_path() -> Path:
    return data_dir() / "facestack.db"


def facestack_db_url() -> str:
    """SQLAlchemy URL to hand to FaceStackConfig so facestack stores
    embeddings alongside classcheck's own data."""
    return f"sqlite:///{facestack_db_path()}"


def snapshots_dir() -> Path:
    d = data_dir() / "snapshots"
    d.mkdir(parents=True, exist_ok=True)
    return d


def build_facestack_config():
    """Return a FaceStackConfig pointing at the per-user facestack DB.

    Imported lazily because `facestack` is a heavyish dependency and not
    every caller (e.g. `classcheck-admin list-rooms`) needs it loaded.
    """
    from facestack.config import FaceStackConfig

    return FaceStackConfig(database_url=facestack_db_url())
