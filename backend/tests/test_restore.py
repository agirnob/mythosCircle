"""Epic-6 story 6-2: restore proven — verify-then-apply (AR30, AR13).

The restore core must verify the whole trust chain before any write to
the destination, apply DB + media atomically, and roll back a failed
media swap. Corrupt snapshots fail loudly naming the artifact while the
live state stays byte-unchanged.

Module is stdlib-only like app.core.backup: no app settings imports.
"""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import app.core.restore as restore
from app.core.backup import ARCHIVE_FILENAME, DB_FILENAME, run_snapshot
from app.core.restore import RestoreError, apply_restore, main, verify_snapshot


def _make_world(data_dir: Path, n_rows: int = 3) -> sqlite3.Connection:
    """A WAL-mode DB with committed rows; the -wal file may hold recent
    commits (the consistency point — a raw file copy would miss them,
    the backup/restore path must not)."""
    connection = sqlite3.connect(data_dir / DB_FILENAME)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT)")
    for index in range(1, n_rows + 1):
        connection.execute("INSERT INTO entities (name) VALUES (?)", (f"entity-{index}",))
    connection.commit()
    return connection


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_snapshot(data_dir: Path, media: dict[str, bytes] | None = None, n_rows: int = 3) -> Path:
    """A real 6-1 snapshot dir via run_snapshot (WAL-consistent copy +
    sidecar + manifest + manifest sidecar + media archive)."""
    data_dir.mkdir()
    _make_world(data_dir, n_rows=n_rows).close()
    if media is not None:
        media_root = data_dir / "media"
        for rel, payload in media.items():
            path = media_root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
    return run_snapshot(data_dir, retain=100)


def _live_state(data_dir: Path) -> dict[str, bytes]:
    """Byte map of the live DB + media (snapshot archives under
    backups/ excluded — they are the restore INPUT and must not move)."""
    return {
        str(rel): path.read_bytes()
        for path in sorted(data_dir.rglob("*"))
        if path.is_file() and (rel := path.relative_to(data_dir)).parts[0] != "backups"
    }


def _snapshot_manifest(snapshot_dir: Path) -> dict[str, object]:
    manifest = json.loads((snapshot_dir / "manifest.json").read_text())
    assert isinstance(manifest, dict)
    return manifest


def test_verify_ok_clean_snapshot(tmp_path: Path) -> None:
    """A clean snapshot verifies; verify is read-only — no files created
    in the snapshot dir (a mode=ro WAL open would write -wal/-shm)."""
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media={"campaign-a/portrait.png": b"\x89PNG-1"})
    before = {p.name: p.read_bytes() for p in snapshot_dir.iterdir()}

    manifest = verify_snapshot(snapshot_dir)

    db = manifest["db"]
    assert isinstance(db, dict)
    assert db["file"] == DB_FILENAME
    assert manifest.get("media") is not None
    after = {p.name: p.read_bytes() for p in snapshot_dir.iterdir()}
    assert sorted(after) == sorted(before)
    assert all(after[name] == payload for name, payload in before.items())


def test_verify_cli_reports_and_exits_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media={"a.png": b"x"}, n_rows=4)

    assert main(["verify", str(snapshot_dir)]) == 0

    out = capsys.readouterr().out
    assert "verify OK" in out
    assert DB_FILENAME in out
    assert ", 4 rows" in out
    assert "archive" in out


def test_verify_no_media_reports(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media=None, n_rows=2)

    assert main(["verify", str(snapshot_dir)]) == 0
    assert "no media" in capsys.readouterr().out


def test_bit_flipped_db_fails_before_any_write(tmp_path: Path) -> None:
    """BIT_ROT_DB: a single flipped byte breaks the db.sha256 sidecar
    pin; apply must fail naming the DB with the live state untouched."""
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media={"a.png": b"\x89PNG-live"})
    snapshot_db = snapshot_dir / DB_FILENAME
    raw = bytearray(snapshot_db.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    snapshot_db.write_bytes(bytes(raw))
    live_before = _live_state(data_dir)

    with pytest.raises(RestoreError, match="does not match its sidecar"):
        apply_restore(snapshot_dir, data_dir=data_dir)

    assert _live_state(data_dir) == live_before


def test_tampered_manifest_fails_naming_sidecar(tmp_path: Path) -> None:
    """TAMPERED_MANIFEST: the manifest.json.sha256 pin is the trust
    anchor — a wrong hash breaks the chain before anything is read."""
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media={"a.png": b"x"})
    (snapshot_dir / "manifest.json.sha256").write_text(f"{'0' * 64}  manifest.json\n")
    live_before = _live_state(data_dir)

    with pytest.raises(RestoreError, match="manifest.json"):
        verify_snapshot(snapshot_dir)
    with pytest.raises(RestoreError, match="manifest.json"):
        apply_restore(snapshot_dir, data_dir=data_dir)

    assert _live_state(data_dir) == live_before


def test_torn_archive_fails_naming_archive(tmp_path: Path) -> None:
    """TORN_ARCHIVE: media.tar.gz bytes no longer match the manifest's
    archive_sha256."""
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media={"a.png": b"x"})
    archive = snapshot_dir / ARCHIVE_FILENAME
    raw = bytearray(archive.read_bytes())
    raw[len(raw) // 2] ^= 0xFF
    archive.write_bytes(bytes(raw))
    live_before = _live_state(data_dir)

    with pytest.raises(RestoreError, match="archive"):
        apply_restore(snapshot_dir, data_dir=data_dir)

    assert _live_state(data_dir) == live_before


def test_corrupt_db_structure_hashes_ok_fails_integrity(tmp_path: Path) -> None:
    """CORRUPT_DB_STRUCTURE: every hash passes, but the DB is structurally
    broken — integrity_check is the only gate that can catch it."""
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media={"a.png": b"x"}, n_rows=5)
    snapshot_db = snapshot_dir / DB_FILENAME
    raw = bytearray(snapshot_db.read_bytes())
    # Page 2 (first table page) cell-pointer area — verified to make
    # integrity_check report non-ok without raising.
    for index in range(4104, min(4130, len(raw))):
        raw[index] = 0
    snapshot_db.write_bytes(bytes(raw))
    # Recompose the trust chain over the corrupted bytes so only
    # integrity_check can fail.
    digest = _sha256(snapshot_db)
    (snapshot_dir / f"{DB_FILENAME}.sha256").write_text(f"{digest}  {DB_FILENAME}\n")
    manifest = _snapshot_manifest(snapshot_dir)
    db_block = manifest["db"]
    assert isinstance(db_block, dict)
    db_block["sha256"] = digest
    manifest_path = snapshot_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    (snapshot_dir / "manifest.json.sha256").write_text(f"{_sha256(manifest_path)}  manifest.json\n")
    live_before = _live_state(data_dir)

    with pytest.raises(RestoreError, match="integrity_check"):
        apply_restore(snapshot_dir, data_dir=data_dir)

    assert _live_state(data_dir) == live_before


def test_missing_artifacts_fail_naming_first_missing(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RestoreError, match="manifest.json.sha256"):
        apply_restore(empty, data_dir=tmp_path / "data")

    data_dir = tmp_path / "partial"
    snapshot_dir = _make_snapshot(data_dir, media={"a.png": b"x"})
    (snapshot_dir / DB_FILENAME).unlink()
    with pytest.raises(RestoreError, match="missing snapshot DB"):
        apply_restore(snapshot_dir, data_dir=data_dir)

    data_dir = tmp_path / "archive-missing"
    snapshot_dir = _make_snapshot(data_dir, media={"a.png": b"x"})
    (snapshot_dir / ARCHIVE_FILENAME).unlink()
    with pytest.raises(RestoreError, match="media archive"):
        apply_restore(snapshot_dir, data_dir=data_dir)


def test_apply_clean_snapshot_restores_world(tmp_path: Path) -> None:
    """HAPPY_PATH: verify first, then DB + media swap; the restored DB
    passes integrity_check with the snapshot's row count; no transient
    dirs, no stale WAL siblings."""
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(
        data_dir, media={"campaign-a/portrait.png": b"\x89PNG-snapshot"}, n_rows=3
    )
    # Divert the live world after the snapshot: extra row + different media.
    live = sqlite3.connect(data_dir / DB_FILENAME)
    live.execute("INSERT INTO entities (name) VALUES ('live-extra')")
    live.commit()
    live.close()
    (data_dir / "media" / "campaign-a" / "portrait.png").write_bytes(b"LIVE-DIVERGED")
    (data_dir / "media" / "orphan.bin").write_bytes(b"should-vanish")

    apply_restore(snapshot_dir, data_dir=data_dir)

    # No stale WAL siblings left by the restore itself (asserted before
    # the test's own sqlite open below re-creates them).
    for suffix in ("-wal", "-shm"):
        assert not (data_dir / f"{DB_FILENAME}{suffix}").exists()

    with sqlite3.connect(data_dir / DB_FILENAME) as check:
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert check.execute("SELECT count(*) FROM entities").fetchone()[0] == 3
        assert check.execute("SELECT name FROM entities WHERE id = 1").fetchone()[0] == "entity-1"
    assert (data_dir / "media" / "campaign-a" / "portrait.png").read_bytes() == (
        b"\x89PNG-snapshot"
    )
    assert not (data_dir / "media" / "orphan.bin").exists()
    leftovers = [
        p.name for p in data_dir.iterdir() if "restore-tmp" in p.name or "restore-backup" in p.name
    ]
    assert leftovers == []


def test_destination_db_filename_from_manifest(tmp_path: Path) -> None:
    """The dev DB is mythos.db, not mythoscircle.db — the destination
    name must come from the manifest's db.file, never be hardcoded."""
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    connection = sqlite3.connect(data_dir / "mythos.db")
    connection.execute("CREATE TABLE t (x INTEGER)")
    connection.execute("INSERT INTO t VALUES (7)")
    connection.commit()
    connection.close()
    snapshot_dir = run_snapshot(data_dir, db_file="mythos.db")
    assert (snapshot_dir / "mythos.db").is_file()

    apply_restore(snapshot_dir, data_dir=data_dir)

    with sqlite3.connect(data_dir / "mythos.db") as check:
        assert check.execute("SELECT x FROM t").fetchone() == (7,)
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not (data_dir / "mythoscircle.db").exists()


def test_media_null_snapshot_leaves_media_tree(tmp_path: Path) -> None:
    """NO_MEDIA_SNAPSHOT: manifest media:null — the existing media tree
    is untouched (its manifest rows are the old state by design)."""
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media=None, n_rows=2)
    assert _snapshot_manifest(snapshot_dir)["media"] is None
    (data_dir / "media").mkdir(parents=True)
    kept = data_dir / "media" / "keep.bin"
    kept.write_bytes(b"untouched")

    apply_restore(snapshot_dir, data_dir=data_dir)

    assert kept.read_bytes() == b"untouched"
    with sqlite3.connect(data_dir / DB_FILENAME) as check:
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert check.execute("SELECT count(*) FROM entities").fetchone()[0] == 2


def test_media_swap_failure_rolls_back_pre_swap_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """APPLY_MEDIA_FAILURE: the tmp→live rename fails after the live tree
    was moved aside — the pre-swap tree is restored, no transient dirs
    remain, and the failure surfaces loudly."""
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media={"snapshot-only.png": b"\x89PNG-snap"}, n_rows=1)
    # Diverge the live tree AFTER snapshotting: the snapshot's archive
    # carries snapshot-only.png, the pre-swap live tree only live.png.
    (data_dir / "media" / "snapshot-only.png").unlink()
    (data_dir / "media" / "live.png").write_bytes(b"\x89PNG-live")

    real_rename = restore._rename

    def fail_on_swap(src: Path, dst: Path) -> None:
        if "restore-tmp" in src.parent.name:  # only the tmp→live swap rename
            raise OSError("injected swap failure")
        real_rename(src, dst)

    monkeypatch.setattr(restore, "_rename", fail_on_swap)

    with pytest.raises(OSError, match="injected swap failure"):
        apply_restore(snapshot_dir, data_dir=data_dir)

    # The pre-swap tree is back: the live-only file survived, the
    # snapshot-only file never landed.
    assert (data_dir / "media" / "live.png").read_bytes() == b"\x89PNG-live"
    assert not (data_dir / "media" / "snapshot-only.png").exists()
    leftovers = [
        p.name for p in data_dir.iterdir() if "restore-tmp" in p.name or "restore-backup" in p.name
    ]
    assert leftovers == []


def test_cli_apply_clean_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media={"a.png": b"x"}, n_rows=2)

    assert main(["apply", str(snapshot_dir), "--data-dir", str(data_dir)]) == 0
    assert "restore complete" in capsys.readouterr().out
    with sqlite3.connect(data_dir / DB_FILENAME) as check:
        assert check.execute("SELECT count(*) FROM entities").fetchone()[0] == 2


def test_cli_apply_uses_env_data_dir(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The env contract mirrors backup.py: MYTHOSCIRCLE_DATA_DIR drives
    the destination without a flag."""
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media={"a.png": b"x"}, n_rows=2)
    monkeypatch.setenv("MYTHOSCIRCLE_DATA_DIR", str(data_dir))

    assert main(["apply", str(snapshot_dir)]) == 0
    assert "restore complete" in capsys.readouterr().out
    with sqlite3.connect(data_dir / DB_FILENAME) as check:
        assert check.execute("SELECT count(*) FROM entities").fetchone()[0] == 2


def test_cli_corrupt_snapshot_exits_nonzero_naming_artifact(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media={"a.png": b"x"})
    (snapshot_dir / "manifest.json.sha256").write_text(f"{'0' * 64}  manifest.json\n")
    live_before = _live_state(data_dir)

    assert main(["apply", str(snapshot_dir), "--data-dir", str(data_dir)]) == 1
    err = capsys.readouterr().err
    assert "FAILED" in err
    assert "manifest.json" in err
    assert _live_state(data_dir) == live_before


def test_media_swap_failure_rolls_back_db_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """APPLY_MEDIA_FAILURE, hardened (review round 1): the media swap
    fails after the DB already applied — the WORLD rolls back
    byte-identical, DB and -wal/-shm included; live state before == live
    state after the failed apply."""
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media={"snapshot-only.png": b"\x89PNG-snap"}, n_rows=1)
    # Diverge the live tree AFTER snapshotting: the snapshot's archive
    # carries snapshot-only.png, the pre-swap live tree only live.png —
    # and the live DB carries a distinguishing row so the rollback of the
    # already-applied DB swap is provable (failure to roll back would
    # leave the snapshot's 3-row DB in place).
    (data_dir / "media" / "snapshot-only.png").unlink()
    (data_dir / "media" / "live.png").write_bytes(b"\x89PNG-live")
    diverge = sqlite3.connect(data_dir / DB_FILENAME)
    diverge.execute("INSERT INTO entities (name) VALUES (?)", ("post-snapshot",))
    diverge.commit()
    diverge.close()
    live_pre = _live_state(data_dir)

    real_rename = restore._rename

    def fail_on_swap(src: Path, dst: Path) -> None:
        if "restore-tmp" in src.parent.name:  # only the tmp->live swap rename
            raise OSError("injected swap failure")
        real_rename(src, dst)

    monkeypatch.setattr(restore, "_rename", fail_on_swap)

    with pytest.raises(OSError, match="injected swap failure"):
        apply_restore(snapshot_dir, data_dir=data_dir)

    # The pre-swap tree AND the pre-swap DB came back byte-identical.
    assert (data_dir / "media" / "live.png").read_bytes() == b"\x89PNG-live"
    assert not (data_dir / "media" / "snapshot-only.png").exists()
    assert _live_state(data_dir) == live_pre
    leftovers = [
        p.name
        for p in data_dir.iterdir()
        if "restore-tmp" in p.name or "restore-backup" in p.name or "restore-pre" in p.name
    ]
    assert leftovers == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda m: m.__setitem__("db", {"file": "mythoscircle.db"}),  # missing sha256
        lambda m: m.__setitem__("db", {"file": "x.db", "sha256": 42}),  # non-string sha256
        lambda m: m.__setitem__("db", {"file": "sub/dir.db", "sha256": "0" * 64}),  # path escape
        lambda m: m.__setitem__("db", {"file": "..", "sha256": "0" * 64}),  # dotdot
        lambda m: m.__setitem__("media", {"archive_sha256": "0" * 64}),  # missing archive
        lambda m: m.__setitem__("media", {"archive": "a/../x.tar.gz", "archive_sha256": "0" * 64}),
        lambda m: m.__setitem__("db", "not-a-block"),  # non-dict db
    ],
)
def test_malformed_manifest_blocks_fail_loudly(tmp_path: Path, mutate: object) -> None:
    """Malformed-manifest guards (review round 1): every structural
    violation raises RestoreError naming the artifact — never a
    KeyError/TypeError traceback — and nothing is applied."""
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, n_rows=2)
    manifest_path = snapshot_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    mutate(manifest)  # type: ignore[operator]
    manifest_path.write_text(json.dumps(manifest))
    # Refresh the manifest sidecar so the tamper is structural, not the pin.
    (snapshot_dir / "manifest.json.sha256").write_text(
        f"{restore._sha256_file(manifest_path)}  manifest.json\n"
    )

    with pytest.raises(RestoreError):
        apply_restore(snapshot_dir, data_dir=data_dir)


def test_cli_verify_failure_names_artifact_and_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """CLI verify failure path (6-5 gate instrument): a corrupt snapshot
    makes `verify` exit 1 with an artifact-naming stderr line — same
    contract as apply's, on the command the gate story will run."""
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, n_rows=2)
    (snapshot_dir / "manifest.json.sha256").write_text(f"{'0' * 64}  manifest.json\n")
    live_before = _live_state(data_dir)

    assert main(["verify", str(snapshot_dir), "--data-dir", str(data_dir)]) == 1
    err = capsys.readouterr().err
    assert "FAILED" in err
    assert "manifest.json" in err
    assert _live_state(data_dir) == live_before
