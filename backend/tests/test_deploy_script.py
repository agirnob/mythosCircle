"""Deploy-script tests (spec-1.7): the stage dry-run builds the expected
tree; scripts lint; the backup flock guard is present.
"""

import os
import sqlite3
import subprocess
from pathlib import Path

from app.core.backup import DB_FILENAME, run_snapshot

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"


def test_deploy_scripts_syntax() -> None:
    for script in ("deploy.sh", "backup.sh", "restore.sh", "restore.docker.sh"):
        result = subprocess.run(
            ["bash", "-n", str(DEPLOY / script)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"{script} syntax error: {result.stderr}"


def test_deploy_stage_dry_run_builds_tree(tmp_path: Path) -> None:
    """DEPLOY_STAGE installs Caddyfile, unit, config, env, cron, and the
    frontend build under the stage root — the full flow without root."""
    stage = tmp_path / "stage"
    # Dependency resolution and frontend compilation have their own CI jobs.
    # Exercise installation without network downloads or altering node_modules.
    binaries = tmp_path / "bin"
    binaries.mkdir()
    (binaries / "uv").write_text("#!/bin/sh\nmkdir -p .venv\n")
    (binaries / "npm").write_text(
        '#!/bin/sh\nwhile [ "$#" -gt 0 ]; do\n'
        'if [ "$1" = --outDir ]; then shift; mkdir -p "$1"; '
        'printf "<!DOCTYPE html>" > "$1/index.html"; fi\nshift\ndone\n'
    )
    for binary in binaries.iterdir():
        binary.chmod(0o755)
    env = {
        "DEPLOY_STAGE": str(stage),
        "SKIP_SERVICE": "1",
        "PATH": f"{binaries}:/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
    }
    result = subprocess.run(
        ["bash", str(DEPLOY / "deploy.sh")],
        capture_output=True,
        text=True,
        env=env,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, f"deploy failed:\n{result.stdout}\n{result.stderr}"
    # Expected install targets under the stage.
    assert (stage / "etc/caddy/Caddyfile").exists()
    assert (stage / "etc/systemd/system/mythoscircle.service").exists()
    assert (stage / "etc/mythoscircle/config.toml").exists()
    assert (stage / "etc/mythoscircle/mythoscircle.env").exists()
    assert (stage / "etc/cron.d/mythoscircle-backup").exists()
    assert (stage / "var/www/mythoscircle/dist/index.html").exists()
    # Backend + scripts are provisioned (review round 1: the unit's
    # ExecStart and the cron's backup.sh target these paths).
    assert (stage / "opt/mythoscircle/backend/app/main.py").exists()
    assert (stage / "opt/mythoscircle/deploy/backup.sh").exists()
    assert (stage / "opt/mythoscircle/deploy/restore.sh").exists()
    assert (stage / "opt/mythoscircle/deploy/restore.docker.sh").exists()
    # The env file carries the non-secret config values + the secrets marker.
    env_text = (stage / "etc/mythoscircle/mythoscircle.env").read_text()
    assert "SECRETS" in env_text
    assert "MYTHOSCIRCLE_MAX_PENDING_PER_CAMPAIGN=" in env_text
    assert "MYTHOSCIRCLE_LLM_ENDPOINT=" in env_text


def test_backup_has_flock_guard() -> None:
    text = (DEPLOY / "backup.sh").read_text()
    assert "flock -n" in text  # single-instance guard (deferred 1.2, fixed 1.7)


def test_backup_flock_second_run_exits_uncontended() -> None:
    """BACKUP_FLOCK behavior: with the lock held externally, backup.sh
    exits 1 with the explicit message; uncontended it runs (review round
    1 — previously only the literal 'flock -n' text was pinned)."""
    import tempfile

    data_dir = Path(tempfile.mkdtemp())
    env = {
        "MYTHOSCIRCLE_DATA_DIR": str(data_dir),
        "PATH": "/usr/bin:/bin",
    }
    script = str(DEPLOY / "backup.sh")
    # Uncontended with no DB: clean skip (exit 0).
    first = subprocess.run(["bash", script], capture_output=True, text=True, env=env)
    assert first.returncode == 0
    # Hold the lock, then run: must exit 1 with the "already running" message.
    lock_file = data_dir / "backup.lock"
    import fcntl

    with lock_file.open("w") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        second = subprocess.run(["bash", script], capture_output=True, text=True, env=env)
        assert second.returncode == 1
        assert "already running" in second.stderr


def test_caddyfile_serves_tunnel_origin_and_proxies() -> None:
    """/api and /ws proxy to the loopback service; the site is a plain-HTTP
    tunnel origin (TLS lives at the Cloudflare edge — the CGNAT host has
    no inbound 80/443 for ACME); the frontend web root is set (acceptance
    criterion 2, review round 1 — the old 'tls' substring matched only a
    comment)."""
    caddy = (DEPLOY / "Caddyfile").read_text()
    assert "http://world.miscco.uk" in caddy
    assert "reverse_proxy /api/* 127.0.0.1:8000" in caddy
    assert "reverse_proxy /ws/* 127.0.0.1:8000" in caddy
    assert "root * /var/www/mythoscircle/dist" in caddy


# --- Story 6-2: restore wrapper behavior (verify-then-apply) ---------------
# The headline AR30/NFR5 property lives in shell glue: the unit may only
# stop AFTER a failed or successful verify, chown runs before restart, and
# a failed apply leaves the service/container stopped. bash -n cannot see
# order — these tests stub the side-effecting binaries on PATH and record
# their invocation sequence (6-2 review round 1).


def _make_world(data_dir: Path, n_rows: int = 3) -> sqlite3.Connection:
    connection = sqlite3.connect(data_dir / DB_FILENAME)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE entities (id INTEGER PRIMARY KEY, name TEXT)")
    for index in range(1, n_rows + 1):
        connection.execute("INSERT INTO entities (name) VALUES (?)", (f"entity-{index}",))
    connection.commit()
    return connection


def _make_snapshot(data_dir: Path, media: dict[str, bytes] | None = None, n_rows: int = 3) -> Path:
    """A real 6-1 snapshot dir (same shape as test_restore's helper)."""
    data_dir.mkdir()
    _make_world(data_dir, n_rows=n_rows).close()
    if media is not None:
        for rel, payload in media.items():
            path = data_dir / "media" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
    return run_snapshot(data_dir, retain=100)


def _stub(tmp_path: Path, name: str, body: str) -> None:
    script = tmp_path / "stubs" / name
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(body)
    script.chmod(0o755)


def _wrapper_env(tmp_path: Path, data_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PATH"] = f"{tmp_path / 'stubs'}:{env['PATH']}"
    env["MYTHOSCIRCLE_DATA_DIR"] = str(data_dir)
    return env


def _live_map(data_dir: Path) -> dict[str, bytes]:
    return {
        str(rel): p.read_bytes()
        for p in sorted(data_dir.rglob("*"))
        if p.is_file() and (rel := p.relative_to(data_dir)).parts[0] != "backups"
    }


def _db_rows(data_dir: Path) -> int:
    with sqlite3.connect(data_dir / DB_FILENAME) as check:
        return check.execute("SELECT count(*) FROM entities").fetchone()[0]


def test_restore_sh_verify_fails_before_stop(tmp_path: Path) -> None:
    """A corrupt snapshot fails the verify step: the unit is NEVER stopped
    and the live world stays byte-unchanged (AR30)."""
    systemctl_log = tmp_path / "stubs" / "systemctl.log"
    _stub(tmp_path, "systemctl", f'printf \'systemctl %s\\n\' "$*" >> "{systemctl_log}"\nexit 0\n')
    _stub(tmp_path, "id", "printf '0\\n'\n")

    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media={"a.png": b"\x89PNG-a"}, n_rows=3)
    live_pre = _live_map(data_dir)
    # Bit-flip the snapshot DB: the sidecar/manifest pins must reject it.
    db = snapshot_dir / DB_FILENAME
    payload = bytearray(db.read_bytes())
    payload[len(payload) // 2] ^= 0xFF
    db.write_bytes(bytes(payload))

    result = subprocess.run(
        ["bash", str(DEPLOY / "restore.sh"), str(snapshot_dir)],
        capture_output=True,
        text=True,
        env=_wrapper_env(tmp_path, data_dir),
    )
    assert result.returncode != 0
    assert "verify FAILED" in result.stderr
    assert not systemctl_log.exists() or systemctl_log.read_text() == "", (
        f"unit was stopped despite a corrupt snapshot:\n{systemctl_log.read_text()}"
    )
    assert _live_map(data_dir) == live_pre


def test_restore_sh_clean_verify_stop_chown_start(tmp_path: Path) -> None:
    """A clean snapshot: verify first, then stop -> apply -> chown ->
    start, and the live world matches the snapshot afterwards (incl. the
    media swap)."""
    systemctl_log = tmp_path / "stubs" / "systemctl.log"
    chown_log = tmp_path / "stubs" / "chown.log"
    _stub(tmp_path, "systemctl", f'printf \'systemctl %s\\n\' "$*" >> "{systemctl_log}"\nexit 0\n')
    _stub(tmp_path, "id", "printf '0\\n'\n")
    _stub(tmp_path, "chown", f'printf \'chown %s\\n\' "$*" >> "{chown_log}"\nexit 0\n')

    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, media={"a.png": b"\x89PNG-a"}, n_rows=3)
    # Diverge the live world AFTER snapshotting: an extra row and an extra
    # media file the restore must roll back.
    connection = sqlite3.connect(data_dir / DB_FILENAME)
    connection.execute("INSERT INTO entities (name) VALUES (?)", ("later-row",))
    connection.commit()
    connection.close()
    (data_dir / "media" / "later.bin").write_bytes(b"later")

    result = subprocess.run(
        ["bash", str(DEPLOY / "restore.sh"), str(snapshot_dir)],
        capture_output=True,
        text=True,
        env=_wrapper_env(tmp_path, data_dir),
    )
    assert result.returncode == 0, f"restore.sh failed:\n{result.stdout}\n{result.stderr}"
    commands = systemctl_log.read_text().splitlines()
    assert commands == [
        "systemctl stop mythoscircle.service",
        "systemctl start mythoscircle.service",
    ], f"unexpected unit lifecycle:\n{systemctl_log.read_text()}"
    assert "chown -R mythoscircle:mythoscircle" in chown_log.read_text()
    assert _db_rows(data_dir) == 3
    assert (data_dir / "media" / "a.png").exists()
    assert not (data_dir / "media" / "later.bin").exists()


def test_restore_docker_sh_verify_fails_before_stop(tmp_path: Path) -> None:
    """The docker wrapper must not stop the api container unless the
    snapshot verifies (review round 1: the stop/verify order was only
    bash -n'd before)."""
    docker_log = tmp_path / "stubs" / "docker.log"
    _stub(
        tmp_path,
        "docker",
        f"""#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "{docker_log}"
if [ "$1" = inspect ]; then printf 'mock-api:local\\n'; exit 0; fi
for arg in "$@"; do [ "$arg" = verify ] && exit 1; done
exit 0
""",
    )
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, n_rows=2)

    result = subprocess.run(
        ["bash", str(DEPLOY / "restore.docker.sh"), str(snapshot_dir)],
        capture_output=True,
        text=True,
        env=_wrapper_env(tmp_path, data_dir),
    )
    assert result.returncode != 0
    assert "verify FAILED" in result.stderr
    lines = docker_log.read_text().splitlines()
    assert "docker stop" not in " ".join(lines), f"container stopped on corrupt snapshot:\n{lines}"


def test_restore_docker_sh_apply_failure_leaves_stopped(tmp_path: Path) -> None:
    """A failed apply leaves the container stopped and never starts it."""
    docker_log = tmp_path / "stubs" / "docker.log"
    _stub(
        tmp_path,
        "docker",
        f"""#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "{docker_log}"
if [ "$1" = inspect ]; then printf 'mock-api:local\\n'; exit 0; fi
for arg in "$@"; do [ "$arg" = apply ] && exit 1; done
exit 0
""",
    )
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, n_rows=2)

    result = subprocess.run(
        ["bash", str(DEPLOY / "restore.docker.sh"), str(snapshot_dir)],
        capture_output=True,
        text=True,
        env=_wrapper_env(tmp_path, data_dir),
    )
    assert result.returncode != 0
    assert "left STOPPED" in result.stderr
    lines = docker_log.read_text().splitlines()
    assert any("apply" in line for line in lines)
    assert not any(line.startswith("docker start") for line in lines)


def test_restore_docker_sh_clean_lifecycle(tmp_path: Path) -> None:
    """Clean snapshot: inspect -> run verify -> stop -> run apply -> start,
    in exactly that order."""
    docker_log = tmp_path / "stubs" / "docker.log"
    _stub(
        tmp_path,
        "docker",
        f"""#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "{docker_log}"
if [ "$1" = inspect ]; then printf 'mock-api:local\\n'; fi
exit 0
""",
    )
    data_dir = tmp_path / "data"
    snapshot_dir = _make_snapshot(data_dir, n_rows=2)

    result = subprocess.run(
        ["bash", str(DEPLOY / "restore.docker.sh"), str(snapshot_dir)],
        capture_output=True,
        text=True,
        env=_wrapper_env(tmp_path, data_dir),
    )
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    lines = docker_log.read_text().splitlines()
    assert "inspect" in " ".join(lines)
    # The verify run must precede the stop; the apply must precede start.
    assert " docker stop " in f" {' '.join(lines)} "
    assert " docker start " in f" {' '.join(lines)} "
    verify_run = next(i for i, line in enumerate(lines) if " verify " in line)
    apply_run = next(i for i, line in enumerate(lines) if " apply " in line)
    stop = next(i for i, line in enumerate(lines) if line.startswith("docker stop"))
    start = next(i for i, line in enumerate(lines) if line.startswith("docker start"))
    assert verify_run < stop < apply_run < start
