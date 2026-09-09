"""Deploy-script tests (spec-1.7): the stage dry-run builds the expected
tree; scripts lint; the backup flock guard is present.
"""

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"


def test_deploy_scripts_syntax() -> None:
    for script in ("deploy.sh", "backup.sh", "restore.sh"):
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
    env = {
        "DEPLOY_STAGE": str(stage),
        "SKIP_SERVICE": "1",
        "PATH": "/usr/bin:/bin:/usr/local/bin",
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
