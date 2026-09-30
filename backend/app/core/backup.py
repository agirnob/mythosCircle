"""Nightly snapshot with integrity (epic-6 story 6-1; AR13, AR30).

A snapshot is a consistent SQLite backup through the sqlite3 *online
backup API* (``Connection.backup`` — never a raw WAL-file copy: the
live ``-wal`` file holds recent commits, and a plain copy silently
misses them), plus the media tree, with:

- ``mythoscircle.db.sha256`` — the DB checksum, written at snapshot
  creation (AR30),
- ``manifest.json`` — the media manifest: DB checksum + the archive
  checksum + every media file's path/size/sha256, plus a
  ``manifest.json.sha256`` sidecar covering the manifest itself. This
  is the trust chain story 6-2's restore verifies against.

The snapshot lands in a dated dir under the backup root (a second local
location relative to the live DB + media; default ``<data-dir>/backups``).
Oldest snapshots beyond the retention window are pruned (default 14).

One lock guard no matter the entrypoint: ``deploy/backup.sh`` keeps its
bash ``flock`` (test-pinned); this module re-flocks the SAME lockfile
(``<data-dir>/backup.lock``) for direct invocations — flock(2) is
per-fd, so both interoperate.

Usage (stdlib only — safe inside the api container):
    python -m app.core.backup --data-dir /var/lib/mythoscircle
    # env overrides: MYTHOSCIRCLE_MEDIA_DIR, MYTHOSCIRCLE_BACKUP_DIR,
    # MYTHOSCIRCLE_BACKUP_RETENTION
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
import time
from pathlib import Path

BACKUP_SCHEMA_VERSION = 1
DEFAULT_RETENTION = 14
DB_FILENAME = "mythoscircle.db"
ARCHIVE_FILENAME = "media.tar.gz"
LOCK_FILENAME = "backup.lock"
# Env keys (AD-22: env > config > default — secrets never; these are
# paths + a count).
DATA_DIR_ENV = "MYTHOSCIRCLE_DATA_DIR"
DB_FILE_ENV = "MYTHOSCIRCLE_DB_FILE"
MEDIA_DIR_ENV = "MYTHOSCIRCLE_MEDIA_DIR"
BACKUP_DIR_ENV = "MYTHOSCIRCLE_BACKUP_DIR"
RETENTION_ENV = "MYTHOSCIRCLE_BACKUP_RETENTION"
DEFAULT_DATA_DIR = "/var/lib/mythoscircle"


class BackupBusy(RuntimeError):
    """Another snapshot holds the lockfile — a manual run raced the cron."""


class NoDatabase(FileNotFoundError):
    """No database at <data-dir>/mythoscircle.db yet — app never ran."""


def _sha256_file(path: Path) -> str:
    """Streaming sha256 over a possibly-large media file (1 MiB chunks)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def _unique_snapshot_dir(backup_root: Path, stamp: str) -> Path:
    """A dated dir; suffix \u201c-1\u201d, \u201c-2\u201d\u2026 on same-second collisions
    so a manual + cron run never clobber each other's snapshot."""
    candidate = backup_root / stamp
    suffix = 0
    while candidate.exists():
        suffix += 1
        candidate = backup_root / f"{stamp}-{suffix}"
    return candidate


def _snapshot_db(live_db: Path, snapshot_dir: Path, db_file: str) -> tuple[str, int]:
    """Consistent copy through the SQLite online backup API.

    The source is opened read-only via URI even in WAL mode: the backup
    API runs a read transaction, so a live writer (the api's WAL) never
    blocks or corrupts the copy.
    """
    destination = snapshot_dir / db_file
    source = sqlite3.connect(f"file:{live_db}?mode=ro", uri=True)
    try:
        with sqlite3.connect(str(destination)) as target:
            source.backup(target)
    finally:
        source.close()
    return _sha256_file(destination), destination.stat().st_size


def _media_entries(media_dir: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in sorted(media_dir.rglob("*")):
        if path.is_file():
            entries.append(
                {
                    "path": path.relative_to(media_dir).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256_file(path),
                }
            )
    return entries


def _snapshot_media(media_dir: Path, snapshot_dir: Path) -> dict[str, object] | None:
    """Archive the media tree (restore.sh's contract) + hash list."""
    if not media_dir.is_dir():
        return None
    archive = snapshot_dir / ARCHIVE_FILENAME
    with tarfile.open(archive, "w:gz") as tar:
        # The tree is the operator's own media dir; entries are listed
        # (and hashed) individually in the manifest anyway.
        tar.add(media_dir, arcname="media")
    entries = _media_entries(media_dir)
    return {
        "archive": ARCHIVE_FILENAME,
        "archive_sha256": _sha256_file(archive),
        "archive_size": archive.stat().st_size,
        "file_count": len(entries),
        "files": entries,
    }


def _write_sidecars(path: Path) -> None:
    """sha256 of a snapshot artifact, one line next to it (AR30)."""
    digest = _sha256_file(path)
    sidecar = path.with_suffix(path.suffix + ".sha256")
    sidecar.write_text(f"{digest}  {path.name}\n")


def prune_backups(backup_root: Path, retain: int) -> list[Path]:
    """Drop snapshots beyond the retention window; newest kept first."""
    kept: list[Path] = []
    for directory in sorted(backup_root.glob("*"), reverse=True):
        if not directory.is_dir():
            continue
        if len(kept) >= retain:
            # The whole snapshot dir — db, sidecars, manifest, archive.
            shutil.rmtree(directory)
        else:
            kept.append(directory)
    return kept


def run_snapshot(
    data_dir: Path,
    media_dir: Path | None = None,
    backup_dir: Path | None = None,
    db_file: str = DB_FILENAME,
    retain: int = DEFAULT_RETENTION,
) -> Path:
    """Take one nightly snapshot; returns the snapshot dir.

    Raises BackupBusy while another run holds the lock; NoDatabase when
    the app has never created its DB (the cron skips cleanly).
    """
    data_dir = Path(data_dir)
    backup_root = Path(backup_dir) if backup_dir is not None else data_dir / "backups"
    media_root = Path(media_dir) if media_dir is not None else data_dir / "media"
    live_db = data_dir / db_file

    lock_path = data_dir / LOCK_FILENAME
    with lock_path.open("a") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BackupBusy("another backup is already running -- exiting") from exc
        try:
            if not live_db.is_file():
                raise NoDatabase(f"no database at {live_db} yet -- skipping")
            backup_root.mkdir(parents=True, exist_ok=True)
            snapshot_dir = _unique_snapshot_dir(backup_root, _stamp())
            snapshot_dir.mkdir(parents=True)
            db_sha256, db_size = _snapshot_db(live_db, snapshot_dir, db_file)
            _write_sidecars(snapshot_dir / db_file)

            media = _snapshot_media(media_root, snapshot_dir)
            manifest = {
                "schema_version": BACKUP_SCHEMA_VERSION,
                "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "db": {
                    "file": db_file,
                    "sha256": db_sha256,
                    "size": db_size,
                },
                "media": media,
                "retention": retain,
            }
            manifest_path = snapshot_dir / "manifest.json"
            manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
            _write_sidecars(manifest_path)

            prune_backups(backup_root, retain)
            return snapshot_dir
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="mythosCircle nightly snapshot (6-1)")
    parser.add_argument(
        "--data-dir",
        default=os.environ.get(DATA_DIR_ENV) or DEFAULT_DATA_DIR,
        help="dir holding mythoscircle.db + media/ + backup lock (env MYTHOSCIRCLE_DATA_DIR)",
    )
    parser.add_argument(
        "--media-dir",
        default=os.environ.get(MEDIA_DIR_ENV) or None,
        help="media root (default <data-dir>/media; env MYTHOSCIRCLE_MEDIA_DIR)",
    )
    parser.add_argument(
        "--backup-dir",
        default=os.environ.get(BACKUP_DIR_ENV) or None,
        help="snapshot root (default <data-dir>/backups; env MYTHOSCIRCLE_BACKUP_DIR)",
    )
    parser.add_argument(
        "--db-file",
        default=os.environ.get(DB_FILE_ENV) or DB_FILENAME,
        help="live DB filename inside data-dir (default mythoscircle.db; "
        "env MYTHOSCIRCLE_DB_FILE — the dev checkout's DB is data/mythos.db)",
    )
    parser.add_argument(
        "--retain",
        type=int,
        default=int(os.environ.get(RETENTION_ENV) or DEFAULT_RETENTION),
        help=f"snapshots to keep (default {DEFAULT_RETENTION}; env MYTHOSCIRCLE_BACKUP_RETENTION)",
    )
    args = parser.parse_args(argv)

    try:
        snapshot_dir = run_snapshot(
            Path(args.data_dir),
            media_dir=Path(args.media_dir) if args.media_dir else None,
            backup_dir=Path(args.backup_dir) if args.backup_dir else None,
            db_file=args.db_file,
            retain=args.retain,
        )
    except BackupBusy as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except NoDatabase as exc:
        print(str(exc), file=sys.stderr)
        return 0
    print(f"mythoscircle backup complete: {snapshot_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
