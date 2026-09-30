"""Epic-6 story 6-1: nightly snapshot with integrity (AR13/AR30).

The module is stdlib-only and standalone: no app settings imports, so
these tests never touch the config pins in conftest.
"""

import fcntl
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.core.backup import (
    ARCHIVE_FILENAME,
    DB_FILENAME,
    BackupBusy,
    NoDatabase,
    main,
    prune_backups,
    run_snapshot,
)


def _make_world(data_dir: Path, n_rows: int = 3) -> sqlite3.Connection:
    """A WAL-mode DB with committed rows; the -wal file may hold recent
    commits (the test's consistency point — a raw file copy would miss
    them, the backup API must not)."""
    connection = sqlite3.connect(data_dir / DB_FILENAME)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT)")
    for index in range(1, n_rows + 1):
        connection.execute("INSERT INTO entities (name) VALUES (?)", (f"entity-{index}",))
    connection.commit()
    return connection


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def test_snapshot_artifacts_integrity(tmp_path: Path) -> None:
    """The snapshot carries the DB (backup API, WAL-consistent), a
    checksum written at creation (AR30), and a media manifest whose
    hashes match the files on disk."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    writer = _make_world(data_dir, n_rows=5)
    media = data_dir / "media"
    (media / "campaign-a").mkdir(parents=True)
    (media / "campaign-a" / "portrait.png").write_bytes(b"\x89PNG-1")
    (media / "portrait2.png").write_bytes(b"\x89PNG-2")

    snapshot_dir = run_snapshot(data_dir)

    db = snapshot_dir / DB_FILENAME
    assert db.is_file()
    # WAL consistency: the committed rows must exist in the backup even
    # though the writer connection still holds the WAL open.
    with sqlite3.connect(db) as check:
        rows = check.execute("SELECT count(*) FROM entities").fetchone()[0]
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert rows == 5

    # Checksum sidecar, matching the DB, written at creation.
    sidecar = snapshot_dir / f"{DB_FILENAME}.sha256"
    assert sidecar.read_text().split()[0] == _sha256(db)

    manifest = json.loads((snapshot_dir / "manifest.json").read_text())
    assert manifest["schema_version"] == 1
    assert manifest["db"]["sha256"] == _sha256(db)
    assert manifest["db"]["size"] == db.stat().st_size
    # Manifest sidecar covers the manifest (the trust chain for 6-2).
    manifest_sidecar = snapshot_dir / "manifest.json.sha256"
    assert manifest_sidecar.read_text().split()[0] == _sha256(snapshot_dir / "manifest.json")

    # Media manifest: archive + every file's path/size/sha256.
    media_info = manifest["media"]
    assert media_info["archive"] == ARCHIVE_FILENAME
    assert media_info["archive_sha256"] == _sha256(snapshot_dir / ARCHIVE_FILENAME)
    assert media_info["file_count"] == 2
    by_path = {entry["path"]: entry for entry in media_info["files"]}
    assert set(by_path) == {"campaign-a/portrait.png", "portrait2.png"}
    assert by_path["campaign-a/portrait.png"]["sha256"] == _sha256(
        media / "campaign-a" / "portrait.png"
    )
    assert by_path["portrait2.png"]["sha256"] == _sha256(media / "portrait2.png")
    writer.close()


def test_snapshot_without_media_writes_null_manifest(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _make_world(data_dir)
    snapshot_dir = run_snapshot(data_dir)
    manifest = json.loads((snapshot_dir / "manifest.json").read_text())
    assert manifest["media"] is None
    assert not (snapshot_dir / ARCHIVE_FILENAME).exists()


def test_snapshot_no_database_raises(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    with pytest.raises(NoDatabase):
        run_snapshot(data_dir)


def test_snapshot_lock_guard(tmp_path: Path) -> None:
    """A concurrent run must refuse — the cron may not double-run (the
    bash wrapper's flock and the module's flock share the same file)."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _make_world(data_dir)
    lock = data_dir / "backup.lock"
    with lock.open("w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BackupBusy):
            run_snapshot(data_dir)


def test_retention_prunes_oldest(tmp_path: Path) -> None:
    backup_root = tmp_path / "backups"
    backup_root.mkdir()
    for day in range(15):
        (backup_root / f"20260901T010000Z-{day:02d}").mkdir()
    kept = prune_backups(backup_root, retain=14)
    remaining = sorted(p.name for p in backup_root.iterdir())
    assert len(remaining) == 14
    assert len(kept) == 14
    # The pruned one is the lexicographically smallest (oldest stamp).
    assert "20260901T010000Z-00" not in remaining


def test_custom_db_filename(tmp_path: Path) -> None:
    """The dev checkout's DB is data/mythos.db, not mythoscircle.db —
    the operator must be able to point the snapshot at it (--db-file /
    MYTHOSCIRCLE_DB_FILE) without symlinking."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    connection = sqlite3.connect(data_dir / "mythos.db")
    connection.execute("CREATE TABLE t (x INTEGER)")
    connection.execute("INSERT INTO t VALUES (42)")
    connection.commit()
    connection.close()
    with pytest.raises(NoDatabase):
        run_snapshot(data_dir)
    snapshot_dir = run_snapshot(data_dir, db_file="mythos.db")
    with sqlite3.connect(snapshot_dir / "mythos.db") as check:
        assert check.execute("SELECT x FROM t").fetchone() == (42,)


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    # No DB yet: clean skip (exit 0) like the bash wrapper's pre-check.
    assert main(["--data-dir", str(data_dir)]) == 0
    _make_world(data_dir)
    # Success prints the snapshot dir.
    assert main(["--data-dir", str(data_dir)]) == 0
    assert "mythoscircle backup complete" in capsys.readouterr().out
    # lock held: exit 1 with the explicit message.
    lock = data_dir / "backup.lock"
    with lock.open("w") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert main(["--data-dir", str(data_dir)]) == 1
        assert "already running" in capsys.readouterr().err


@pytest.mark.parametrize("explicit", [False, True])
def test_cli_directory_env_defaults_and_flag_precedence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit: bool
) -> None:
    data = tmp_path / "data"
    data.mkdir()
    writer = _make_world(data)
    env_media, env_backup = tmp_path / "env-media", tmp_path / "env-backups"
    flag_media, flag_backup = tmp_path / "flag-media", tmp_path / "flag-backups"
    for media in (env_media, flag_media):
        media.mkdir()
        (media / "portrait.png").write_bytes(media.name.encode())
    monkeypatch.setenv("MYTHOSCIRCLE_MEDIA_DIR", str(env_media))
    monkeypatch.setenv("MYTHOSCIRCLE_BACKUP_DIR", str(env_backup))
    args = ["--data-dir", str(data)]
    if explicit:
        args += ["--media-dir", str(flag_media), "--backup-dir", str(flag_backup)]
    assert main(args) == 0
    root, media = (flag_backup, flag_media) if explicit else (env_backup, env_media)
    snapshot = next(root.iterdir())
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert manifest["media"]["files"][0]["sha256"] == _sha256(media / "portrait.png")
    writer.close()
