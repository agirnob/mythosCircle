"""Engine/session setup for the world store (AD-13, single writer).

One SQLite database in WAL mode. The app points it at
``/var/lib/mythoscircle/mythoscircle.db`` (override with env
``MYTHOSCIRCLE_DB``); tests point it at scratch files. ``session_scope``
is the single-writer seam: one transaction per block, commit on success,
rollback on any error.
"""

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.store import models

#: Environment variable overriding the database URL.
DB_ENV_VAR = "MYTHOSCIRCLE_DB"
#: Canonical database location (matches deploy/config.toml and the
#: backup/restore scripts). Absolute path, so it is CWD-independent in
#: production; local development overrides with MYTHOSCIRCLE_DB.
DEFAULT_DB_URL = "sqlite:////var/lib/mythoscircle/mythoscircle.db"

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def _configure_connection(dbapi_connection: Any, connection_record: Any) -> None:
    """Per-connection SQLite setup (AD-13, AD-2).

    WAL for the single-writer design; foreign keys enforced at the DB level
    so the schema's ``ForeignKey`` constraints are real, not decorative;
    busy_timeout so a writer blocked behind BEGIN IMMEDIATE waits for the
    lock instead of failing instantly with "database is locked".
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.close()


def _begin_immediate(conn: Any) -> None:
    """Take the SQLite write lock before the first statement of every
    transaction.

    pysqlite's ``isolation_level="SERIALIZABLE"`` only sets
    ``PRAGMA read_uncommitted=0`` — it does NOT emit ``BEGIN IMMEDIATE``
    (verified against the SQLAlchemy 2.0.52 dialect source), so without this
    listener the base-revision check in ``commit_subgraph``/``undo`` would run
    in autocommit and two concurrent commits on the same base could both pass
    ``_check_base`` before either took the write lock (AD-2 silent overwrite).
    A real ``BEGIN IMMEDIATE`` serializes writers at the lock: the loser's
    reads then see the winner's head and reject with ``StaleRevisionError``.
    """
    conn.exec_driver_sql("BEGIN IMMEDIATE")


def init_db(url: str = DEFAULT_DB_URL) -> Engine:
    """Create the schema and return the engine.

    Idempotent for an unchanged URL. A new URL replaces the engine
    (tests re-point per test case); the old engine is disposed so its
    connections and file handles are released.
    """
    global _engine, _session_factory
    if _engine is not None:
        if str(_engine.url) == url:
            return _engine
        _engine.dispose()
    if url.startswith("sqlite:///") and ":memory:" not in url:
        parent = os.path.dirname(url.removeprefix("sqlite:///"))
        if parent:
            os.makedirs(parent, exist_ok=True)
    _engine = create_engine(
        url,
        # AD-2: every transaction starts with BEGIN IMMEDIATE (see
        # _begin_immediate) — the write lock is taken before the
        # base-revision check, so check-then-act is atomic with the write.
        # isolation_level=SERIALIZABLE adds read_uncommitted=0 (snapshot
        # isolation semantics for reads inside the write transaction).
        isolation_level="SERIALIZABLE",
    )
    event.listens_for(_engine, "connect")(_configure_connection)
    event.listens_for(_engine, "begin")(_begin_immediate)
    models.Base.metadata.create_all(_engine)
    _migrate_job_result(_engine)
    _migrate_campaign_seed(_engine)
    _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
    return _engine


def _migrate_job_result(engine: Engine) -> None:
    """Add ``job.result`` to a database created before story 1.4.

    ``create_all`` never ALTERs an existing table, so a 1.3 database would
    fail with ``no such column: job.result`` on any Job SELECT. The store
    owns the schema (AD-13); one idempotent additive column keeps existing
    worlds working. Run after ``create_all`` in ``init_db``.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "job" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("job")}
    if "result" in columns:
        return
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE job ADD COLUMN result JSON"))


def _migrate_campaign_seed(engine: Engine) -> None:
    """Add the AR27 world-seed columns to a pre-1.6 database.

    ``create_all`` never ALTERs an existing table, so a 1.5-era database
    (bare ``name`` campaign table) would fail on any Campaign SELECT after
    the model gains owner/title/description/theme/custom_lore. This runs
    per-column (idempotent — a half-migrated DB finishes on the next
    init), and the legacy ``name`` column is dropped only when the table
    holds no rows, so a naming collision never silently truncates data.
    """
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    if "campaign" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("campaign")}
    additions = {
        "owner_id": "VARCHAR(26)",
        "title": "VARCHAR(300)",
        "description": "TEXT",
        "theme": "VARCHAR(100)",
        "custom_lore": "TEXT",
    }
    with engine.begin() as connection:
        for column, ddl in additions.items():
            if column not in columns:
                connection.execute(text(f"ALTER TABLE campaign ADD COLUMN {column} {ddl}"))
    if "name" in columns:
        with engine.begin() as connection:
            count = connection.execute(text("SELECT COUNT(*) FROM campaign")).scalar_one()
            if count == 0:
                connection.execute(text("ALTER TABLE campaign DROP COLUMN name"))


def get_engine() -> Engine:
    if _engine is None:
        raise RuntimeError("store not initialized — call init_db() first")
    return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction per block (AR3): commit on success, rollback on error."""
    if _session_factory is None:
        raise RuntimeError("store not initialized — call init_db() first")
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def app_db_url() -> str:
    """Database URL for the running app (env-overridable)."""
    return os.environ.get(DB_ENV_VAR, DEFAULT_DB_URL)


def init_app_db() -> Engine:
    """Initialize the store from the app's environment (idempotent)."""
    return init_db(app_db_url())
