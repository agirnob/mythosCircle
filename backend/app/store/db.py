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

from app.core.settings import configured_db_url
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
    _migrate_job_kind(_engine)
    _migrate_proposed_candidate_status(_engine)
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


def _migrate_job_kind(engine: Engine) -> None:
    """Widen the job-kind CHECK constraint on a pre-current database.

    SQLite cannot ALTER a CHECK constraint, so the table is rebuilt with
    the standard constraint-rebuild recipe: capture the table DDL plus the
    job table's explicit index DDLs, create ``job_new`` with the widened
    ``kind`` check, copy the rows, drop the old table, rename, then
    re-create the indexes (their DDL references ``job``, which is valid
    again after the rename). The rebuild derives BOTH the canonical kind
    list and the idempotency test from ``JOB_KINDS`` (function-local
    import — store.jobs imports this package): a job table whose DDL
    already admits every current kind is left untouched; otherwise the
    IN-list is substituted with the full sorted set. One pass therefore
    migrates every older shape — the pre-2.1 3-kind list (spec-2.1), the
    2.1 list missing ``generate`` (spec-3.1), and any future kind added
    to ``JOB_KINDS`` — and ``create_all`` on a fresh database already
    emits the widened constraint.
    """
    import re

    from sqlalchemy import text

    from app.store.jobs import JOB_KINDS

    with engine.begin() as connection:
        row = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='job'")
        ).first()
        table_sql = row[0] if row is not None else None
        if table_sql is None or all(f"'{kind}'" in table_sql for kind in JOB_KINDS):
            return
        index_rows = connection.execute(
            text(
                "SELECT sql FROM sqlite_master WHERE type='index'"
                " AND tbl_name='job' AND sql IS NOT NULL"
            )
        ).fetchall()
        kind_list = ",".join(f"'{kind}'" for kind in sorted(JOB_KINDS))
        new_table_sql = re.sub(
            r"kind IN \([^)]*\)",
            f"kind IN ({kind_list})",
            table_sql,
            count=1,
        ).replace("CREATE TABLE job ", "CREATE TABLE job_new ", 1)
        connection.execute(text(new_table_sql))
        connection.execute(text("INSERT INTO job_new SELECT * FROM job"))
        connection.execute(text("DROP TABLE job"))
        connection.execute(text("ALTER TABLE job_new RENAME TO job"))
        for (index_sql,) in index_rows:
            connection.execute(text(index_sql))


def _migrate_proposed_candidate_status(engine: Engine) -> None:
    """Widen the proposed-candidate status CHECK on a pre-current database.

    Spec-3.2 counterpart of ``_migrate_job_kind`` (same constraint-rebuild
    recipe — SQLite cannot ALTER a CHECK): capture the table DDL plus the
    proposed_candidate table's explicit index DDLs, create
    ``proposed_candidate_new`` with the full lifecycle status list, copy
    the rows, drop the old table, rename, then re-create the indexes.
    Both the canonical status list and the idempotency test derive from
    ``models.PROPOSAL_STATUS``: a table whose DDL already admits every
    current status is left untouched; otherwise the IN-list is
    substituted with the full set. One pass therefore migrates the
    pre-3.2 single-status shape and any future widening — and
    ``create_all`` on a fresh database already emits the widened
    constraint. Belt-and-braces: the repo ships no persistent deploy DB,
    so this only matters for long-lived developer databases.
    """
    import re

    from sqlalchemy import text

    status_list = ",".join(f"'{status}'" for status in sorted(models.PROPOSAL_STATUS))
    with engine.begin() as connection:
        row = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name='proposed_candidate'")
        ).first()
        table_sql = row[0] if row is not None else None
        if table_sql is None or all(f"'{s}'" in table_sql for s in models.PROPOSAL_STATUS):
            return
        index_rows = connection.execute(
            text(
                "SELECT sql FROM sqlite_master WHERE type='index'"
                " AND tbl_name='proposed_candidate' AND sql IS NOT NULL"
            )
        ).fetchall()
        new_table_sql = re.sub(
            r"status IN \([^)]*\)",
            f"status IN ({status_list})",
            table_sql,
            count=1,
        ).replace("CREATE TABLE proposed_candidate ", "CREATE TABLE proposed_candidate_new ", 1)
        connection.execute(text(new_table_sql))
        connection.execute(
            text("INSERT INTO proposed_candidate_new SELECT * FROM proposed_candidate")
        )
        connection.execute(text("DROP TABLE proposed_candidate"))
        connection.execute(text("ALTER TABLE proposed_candidate_new RENAME TO proposed_candidate"))
        for (index_sql,) in index_rows:
            connection.execute(text(index_sql))


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
    """Database URL for the running app (env > config > default, spec-1.7)."""
    if DB_ENV_VAR in os.environ:
        return os.environ[DB_ENV_VAR]
    configured = configured_db_url()
    if configured:
        return f"sqlite:///{configured}"
    return DEFAULT_DB_URL


def init_app_db() -> Engine:
    """Initialize the store from the app's environment (idempotent)."""
    return init_db(app_db_url())
