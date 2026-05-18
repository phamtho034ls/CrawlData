"""Local Prefect settings to reduce SQLite lock issues on Windows."""

from __future__ import annotations

import os
from pathlib import Path


def configure_prefect() -> None:
    """Apply project-local Prefect settings before importing prefect."""
    project_root = Path(__file__).resolve().parent.parent
    prefect_home = project_root / ".prefect"
    prefect_home.mkdir(parents=True, exist_ok=True)
    db_file = prefect_home / "prefect.db"

    os.environ.setdefault("PREFECT_HOME", str(prefect_home))
    os.environ.setdefault(
        "PREFECT_API_DATABASE_CONNECTION_URL",
        f"sqlite+aiosqlite:///{db_file.resolve().as_posix()}?timeout=60",
    )
    os.environ.setdefault("PREFECT_LOGGING_TO_API_ENABLED", "false")
