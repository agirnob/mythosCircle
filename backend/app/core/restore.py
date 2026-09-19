"""Restore proven — verify-then-apply (epic-6 story 6-2; AR30, AR13).

Consumes the 6-1 snapshot format (see ``app.core.backup``): a snapshot
dir holding the DB + its ``.sha256`` sidecar, ``manifest.json`` (DB and
media hashes) + its own sidecar, and optionally ``media.tar.gz``.

Restore is strictly verify-then-apply:

- ``verify`` checks the trust chain in order: ``manifest.json.sha256``
  anchors ``manifest.json`` bytes; the manifest's ``db.sha256`` AND the
  DB's own sidecar pin the DB (two independent pins); ``archive_sha256``
  pins the media archive; ``PRAGMA integrity_check`` on the snapshot DB
  catches structural tears no hash can. Any mismatch, missing artifact,
  or unparseable manifest fails loudly naming the artifact, with ZERO
  writes to the destination (this subcommand is also the 6-5 gate
  instrument).
- ``apply`` verifies first, then copies the DB through the sqlite3
  *online backup API* into ``<dest>.restore-tmp`` in the destination
  directory (never a raw WAL copy — AR13), swaps it in with
  ``os.replace`` (atomic) and removes stale ``-wal``/``-shm`` siblings;
  media is extracted into a temp dir and directory-swapped, restoring
  the pre-swap tree if the swap fails. A ``media:null`` snapshot leaves
  the existing media tree untouched. A post-apply smoke (integrity_check
  + row count) runs before the wrapper restarts the service.

Every snapshot read opens the DB as ``immutable=1`` read-only: the
snapshot is a static artifact whose bytes are hash-pinned, and a plain
``mode=ro`` open of a WAL-mode DB without siblings silently recreates
``-wal``/``-shm`` files next to it.

Stdlib only — runs on the host (deploy/restore.sh) and inside the api
container (deploy/restore.docker.sh). Usage:

    python -m app.core.restore verify <snapshot-dir> [--data-dir DIR]
    python -m app.core.restore apply  <snapshot-dir> [--data-dir DIR]

The destination DB filename comes from the manifest's ``db.file``
(faithful to the snapshot — the dev DB is ``mythos.db``, not
``mythoscircle.db``).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tarfile
from pathlib import Path

from app.core.backup import DATA_DIR_ENV, DEFAULT_DATA_DIR, MEDIA_DIR_ENV

MANIFEST_FILENAME = "manifest.json"
MANIFEST_SIDECAR = "manifest.json.sha256"


class RestoreError(RuntimeError):
    """Verification or apply failure; the message names the artifact."""


def _sha256_file(path: Path) -> str:
    """Streaming sha256 over a possibly-large media file (1 MiB chunks)."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _readonly_connection(db_path: Path) -> sqlite3.Connection:
    """Read-only open that never touches the file.

    ``immutable=1`` skips the WAL machinery entirely: the snapshot is a
    static, hash-pinned artifact, and a plain ``mode=ro`` open of a
    WAL-mode DB without ``-wal``/``-shm`` siblings silently CREATES them
    (verified empirically — fatal for the "verify writes nothing"
    contract).
    """
    return sqlite3.connect(f"file:{db_path}?mode=ro&immutable=1", uri=True)


def _integrity_check(db_path: Path) -> str:
    connection = _readonly_connection(db_path)
    try:
        return str(connection.execute("PRAGMA integrity_check").fetchone()[0])
    except sqlite3.DatabaseError as exc:
        raise RestoreError(f"database {db_path.name} failed integrity_check: {exc}") from exc
    finally:
        connection.close()


def _row_count(db_path: Path) -> int:
    """Total rows across every user table — the apply smoke compares the
    restored DB against this."""
    connection = _readonly_connection(db_path)
    try:
        tables = [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        total = 0
        for table in tables:
            statement = f'SELECT count(*) FROM "{table}"'
            try:
                total += int(connection.execute(statement).fetchone()[0])  # noqa: B608 -- the
                # table name comes from this DB's own sqlite_master (trusted local
                # data on a verified artifact), not from any untrusted input.
            except sqlite3.DatabaseError as exc:
                raise RestoreError(f"database {db_path.name} failed row-count: {exc}") from exc
        return total
    finally:
        connection.close()


def _check_sidecar(sidecar: Path, artifact: Path) -> None:
    """GNU coreutils format ``'<64-hex>  <name>'`` — hash AND name pinned."""
    try:
        fields = sidecar.read_text().strip().split()
    except OSError as exc:
        raise RestoreError(f"cannot read sidecar {sidecar.name}: {exc}") from exc
    if len(fields) != 2 or fields[1] != artifact.name:
        raise RestoreError(f"malformed sidecar {sidecar.name}")
    if fields[0] != _sha256_file(artifact):
        raise RestoreError(f"{artifact.name} does not match its sidecar {sidecar.name}")


def _manifest_db(manifest: dict[str, object], snapshot_dir: Path) -> tuple[str, str]:
    """The snapshot's (db_filename, db_sha256) — validated with loud
    RestoreErrors, never a KeyError/TypeError traceback."""
    db = manifest.get("db")
    if not isinstance(db, dict):
        raise RestoreError(f"malformed manifest in {snapshot_dir}: missing db block")
    db_file = db.get("file")
    if not isinstance(db_file, str) or not db_file or "/" in db_file or db_file in (".", ".."):
        raise RestoreError(f"malformed manifest in {snapshot_dir}: invalid db.file {db_file!r}")
    db_sha256 = db.get("sha256")
    if not isinstance(db_sha256, str) or not db_sha256:
        raise RestoreError(f"malformed manifest in {snapshot_dir}: invalid db.sha256")
    return db_file, db_sha256


def _manifest_media(manifest: dict[str, object], snapshot_dir: Path) -> tuple[str, str] | None:
    """(archive_name, archive_sha256) or None for a media:null snapshot."""
    media = manifest.get("media")
    if media is None:
        return None
    if not isinstance(media, dict):
        raise RestoreError(f"malformed manifest in {snapshot_dir}: invalid media block")
    archive = media.get("archive")
    archive_sha256 = media.get("archive_sha256")
    if not isinstance(archive, str) or not archive or "/" in archive:
        raise RestoreError(
            f"malformed manifest in {snapshot_dir}: invalid media.archive {archive!r}"
        )
    if not isinstance(archive_sha256, str) or not archive_sha256:
        raise RestoreError(f"malformed manifest in {snapshot_dir}: invalid media.archive_sha256")
    return archive, archive_sha256


def verify_snapshot(snapshot_dir: Path) -> dict[str, object]:
    """Verify the whole trust chain; raise RestoreError on any failure.

    Reads only — zero writes to the snapshot or the destination. Returns
    the parsed manifest on success (also the 6-5 gate instrument).
    """
    snapshot_dir = Path(snapshot_dir)
    if not snapshot_dir.is_dir():
        raise RestoreError(f"snapshot dir {snapshot_dir} is not a directory")

    manifest_sidecar = snapshot_dir / MANIFEST_SIDECAR
    manifest_path = snapshot_dir / MANIFEST_FILENAME
    if not manifest_sidecar.is_file():
        raise RestoreError(f"missing manifest sidecar {MANIFEST_SIDECAR} in {snapshot_dir}")
    if not manifest_path.is_file():
        raise RestoreError(f"missing manifest {MANIFEST_FILENAME} in {snapshot_dir}")
    _check_sidecar(manifest_sidecar, manifest_path)

    try:
        manifest = json.loads(manifest_path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RestoreError(f"unparseable manifest {MANIFEST_FILENAME}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RestoreError(f"unparseable manifest {MANIFEST_FILENAME}: not an object")

    db_file, db_sha256 = _manifest_db(manifest, snapshot_dir)
    snapshot_db = snapshot_dir / db_file
    db_sidecar = snapshot_dir / f"{db_file}.sha256"
    if not db_sidecar.is_file():
        raise RestoreError(f"missing db sidecar {db_sidecar.name} in {snapshot_dir}")
    if not snapshot_db.is_file():
        raise RestoreError(f"missing snapshot DB {snapshot_db.name} in {snapshot_dir}")
    _check_sidecar(db_sidecar, snapshot_db)
    if _sha256_file(snapshot_db) != db_sha256:
        raise RestoreError(f"snapshot DB {snapshot_db.name} does not match manifest db.sha256")

    media_pin = _manifest_media(manifest, snapshot_dir)
    if media_pin is not None:
        archive_name, archive_sha256 = media_pin
        archive = snapshot_dir / archive_name
        if not archive.is_file():
            raise RestoreError(f"missing media archive {archive_name} in {snapshot_dir}")
        if _sha256_file(archive) != archive_sha256:
            raise RestoreError(
                f"media archive {archive_name} does not match manifest archive_sha256"
            )

    integrity = _integrity_check(snapshot_db)
    if integrity != "ok":
        raise RestoreError(f"snapshot DB {snapshot_db.name} failed integrity_check: {integrity}")
    return manifest


def _rename(src: Path, dst: Path) -> None:
    """os.rename — module-level so tests can inject media-swap failures."""
    os.rename(src, dst)


def _apply_db(snapshot_db: Path, live_db: Path) -> None:
    """Copy the snapshot DB via the online backup API into a PID-unique
    temp file in the destination directory, then atomic os.replace.

    The live path is EMPTY here — apply_restore moved any pre-swap DB and
    its -wal/-shm siblings aside first — so no stale WAL can survive the
    swap and none needs dropping. ``target.close()`` is explicit: the
    sqlite3 connection context manager commits but does NOT close."""
    tmp = live_db.with_name(f"{live_db.name}.restore-tmp.{os.getpid()}")
    try:
        source = _readonly_connection(snapshot_db)
        try:
            target = sqlite3.connect(str(tmp))
            try:
                source.backup(target)
            finally:
                target.close()
        finally:
            source.close()
        os.replace(tmp, live_db)
    finally:
        if tmp.exists():
            tmp.unlink()


def _db_siblings(live_db: Path) -> tuple[Path, ...]:
    """The live DB path plus its -wal/-shm companions."""
    return tuple(live_db.with_name(live_db.name + suffix) for suffix in ("", "-wal", "-shm"))


def _smoke_check(live_db: Path, expected_rows: int) -> None:
    """Post-apply smoke: the restored DB must pass integrity_check with
    the snapshot's row count (runs before the wrapper restarts the
    service)."""
    integrity = _integrity_check(live_db)
    if integrity != "ok":
        raise RestoreError(f"restored DB {live_db.name} failed integrity_check: {integrity}")
    rows = _row_count(live_db)
    if rows != expected_rows:
        raise RestoreError(
            f"restored DB {live_db.name} row count {rows} != snapshot {expected_rows}"
        )


def _apply_media(snapshot_dir: Path, manifest: dict[str, object], media_root: Path) -> None:
    """Extract the media archive into a temp dir and swap it in; on swap
    failure the pre-swap tree is restored — never a half media tree at
    the live path."""
    media_pin = _manifest_media(manifest, snapshot_dir)
    if media_pin is None:
        return  # media:null snapshots leave the existing tree untouched
    archive_name, _ = media_pin
    archive = snapshot_dir / archive_name
    tmp_root = media_root.with_name(f"{media_root.name}.restore-tmp.{os.getpid()}")
    backup_root = media_root.with_name(f"{media_root.name}.restore-backup.{os.getpid()}")
    had_live = media_root.exists()
    try:
        tmp_root.mkdir(parents=True)
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(tmp_root, filter="data")
        extracted = tmp_root / "media"
        if not extracted.is_dir():
            raise RestoreError(
                f"archive {archive_name} did not extract the media/ tree "
                f"(found: {sorted(p.name for p in tmp_root.iterdir())})"
            )
        if had_live:
            _rename(media_root, backup_root)
        try:
            _rename(extracted, media_root)
        except OSError as swap_exc:
            if had_live:
                try:
                    _rename(backup_root, media_root)
                except OSError as rollback_exc:
                    raise RestoreError(
                        f"media swap failed ({swap_exc}) and rollback also failed "
                        f"({rollback_exc}); pre-swap tree left at {backup_root}"
                    ) from rollback_exc
            raise
        if backup_root.exists():
            shutil.rmtree(backup_root)
    finally:
        if tmp_root.exists():
            shutil.rmtree(tmp_root)


def apply_restore(
    snapshot_dir: Path,
    data_dir: Path | None = None,
    media_dir: Path | None = None,
) -> dict[str, object]:
    """Verify strictly, then apply the DB + media to this host.

    Any verification failure raises before a single write. The pre-swap
    DB — and its -wal/-shm siblings — is moved aside first, so ANY
    apply-stage failure (smoke, media swap, I/O) rolls the world back
    byte-identical: DB and media both revert. Returns the verified
    manifest.
    """
    snapshot_dir = Path(snapshot_dir)
    manifest = verify_snapshot(snapshot_dir)
    data_dir = Path(data_dir) if data_dir is not None else Path(DEFAULT_DATA_DIR)
    media_root = Path(media_dir) if media_dir is not None else data_dir / "media"
    data_dir.mkdir(parents=True, exist_ok=True)

    db_file, _ = _manifest_db(manifest, snapshot_dir)
    snapshot_db = snapshot_dir / db_file
    live_db = data_dir / db_file
    expected_rows = _row_count(snapshot_db)

    had_db = live_db.exists()
    pre_suffix = f".restore-pre.{os.getpid()}"
    if had_db:
        for source in _db_siblings(live_db):
            if source.exists():
                os.replace(source, source.with_name(source.name + pre_suffix))
    try:
        _apply_db(snapshot_db, live_db)
        _smoke_check(live_db, expected_rows)
        _apply_media(snapshot_dir, manifest, media_root)
    except BaseException:
        # Any apply-stage failure: the pre-swap world comes back whole —
        # DB and its -wal/-shm siblings, name-identical.
        for original in _db_siblings(live_db):
            saved = original.with_name(original.name + pre_suffix)
            if saved.exists():
                os.replace(saved, original)
        if not had_db and live_db.exists():
            live_db.unlink()
        raise
    finally:
        for original in _db_siblings(live_db):
            saved = original.with_name(original.name + pre_suffix)
            if saved.exists():
                saved.unlink()
    return manifest


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="restore", description="mythosCircle restore — verify-then-apply (6-2)"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("verify", "verify a snapshot's trust chain; zero writes"),
        ("apply", "verify, then apply the snapshot to this host"),
    ):
        subparser = subparsers.add_parser(command, help=help_text)
        subparser.add_argument("snapshot_dir", help="the 6-1 snapshot dir to restore from")
        subparser.add_argument(
            "--data-dir",
            default=os.environ.get(DATA_DIR_ENV) or DEFAULT_DATA_DIR,
            help="destination data dir holding the live DB (env MYTHOSCIRCLE_DATA_DIR)",
        )
        subparser.add_argument(
            "--media-dir",
            default=os.environ.get(MEDIA_DIR_ENV),
            help="destination media root (default <data-dir>/media; env MYTHOSCIRCLE_MEDIA_DIR)",
        )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    command = args.command or ""
    snapshot_dir = Path(args.snapshot_dir)
    data_dir = Path(args.data_dir)
    media_dir = Path(args.media_dir) if args.media_dir else None
    try:
        if command == "verify":
            manifest = verify_snapshot(snapshot_dir)
            db_file, _ = _manifest_db(manifest, snapshot_dir)
            rows = _row_count(snapshot_dir / db_file)
            media_note = "archive" if manifest.get("media") is not None else "no media"
            print(f"restore verify OK: {snapshot_dir} ({db_file}, {rows} rows, {media_note})")
            return 0
        apply_restore(snapshot_dir, data_dir=data_dir, media_dir=media_dir)
        print(f"restore complete from {snapshot_dir}")
        return 0
    except (RestoreError, OSError, sqlite3.DatabaseError, tarfile.TarError) as exc:
        print(f"restore {command} FAILED: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
