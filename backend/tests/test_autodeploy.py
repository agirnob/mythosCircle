"""Exercise deployment decisions and rollback without touching Docker or a live DB."""

import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "deploy/auto-deploy.sh"
REVISION = "a" * 40


def run_deploy(
    tmp_path: Path, mode: str = "success", *, images_ready: bool = False
) -> tuple[subprocess.CompletedProcess[str], str]:
    binaries = tmp_path / "bin"
    binaries.mkdir(exist_ok=True)
    commands = {
        "git": f"#!/bin/sh\necho {REVISION}\n",
        "sleep": "#!/bin/sh\nexit 0\n",
        "docker": '''#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_LOG"
case "$1" in
  inspect) echo "sha256:old-$4" ;;
  build) [ "$TEST_MODE" != buildfail ] || exit 1 ;;
  image) if [ "$TEST_MODE" = wrongimage ]; then echo wrong; else printf '%040d\\n' 0 | tr 0 a; fi ;;
  exec)
    case "$*" in
      *app.core.backup*) echo 'mythoscircle backup complete: /data/backups/autodeploy/snapshot' ;;
      *) if [ "$TEST_MODE" = busy ]; then echo 1; else echo 0; fi ;;
    esac ;;
esac
exit 0
''',
        "curl": '''#!/usr/bin/env python3
import os, pathlib, sys
p = pathlib.Path(os.environ['TEST_CURL_COUNT'])
count = int(p.read_text()) + 1 if p.exists() else 1
p.write_text(str(count))
if os.environ['TEST_MODE'] == 'healthfail' and count <= 2:
    sys.exit(1)
print('{"status":"ok"}')
''',
    }
    for name, content in commands.items():
        path = binaries / name
        path.write_text(content)
        path.chmod(0o755)
    compose = tmp_path / "compose.yml"
    compose.touch()
    log = tmp_path / "commands.log"
    environment = {
        **os.environ,
        "PATH": f"{binaries}:{os.environ['PATH']}",
        "MYTHOS_AUTO_STATE_DIR": str(tmp_path / "state"),
        "MYTHOS_AUTO_COMPOSE_FILE": str(compose),
        "MYTHOS_AUTO_HEALTH_ATTEMPTS": "2",
        "MYTHOS_AUTO_JOB_WAIT_ATTEMPTS": "2",
        "MYTHOS_AUTO_IMAGES_READY": "1" if images_ready else "0",
        "TEST_LOG": str(log),
        "TEST_CURL_COUNT": str(tmp_path / "curl-count"),
        "TEST_MODE": mode,
    }
    result = subprocess.run(
        ["bash", str(SCRIPT)], env=environment, capture_output=True, text=True, timeout=15
    )
    return result, log.read_text() if log.exists() else ""


def test_success_records_revision_only_after_backup_and_restarts(tmp_path: Path) -> None:
    result, commands = run_deploy(tmp_path)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "state/deployed-revision").read_text().strip() == REVISION
    assert commands.index("stop web") < commands.index("app.core.backup")
    assert commands.index("app.core.backup") < commands.index("up -d --no-deps api")
    assert commands.index("up -d --no-deps api") < commands.index("up -d --no-deps web")


def test_unchanged_revision_does_not_build_or_restart(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    (state / "deployed-revision").write_text(REVISION)
    result, commands = run_deploy(tmp_path)
    assert result.returncode == 0
    assert not commands


def test_active_jobs_defer_without_marking_failed(tmp_path: Path) -> None:
    result, commands = run_deploy(tmp_path, "busy")
    assert result.returncode != 0
    assert "build" not in commands
    assert "stop web" not in commands
    assert not (tmp_path / "state/failed-revision").exists()


def test_health_failure_restores_snapshot_and_old_images(tmp_path: Path) -> None:
    result, commands = run_deploy(tmp_path, "healthfail")
    assert result.returncode != 0
    assert "app.core.restore apply /data/backups/autodeploy/snapshot" in commands
    assert "sha256:old-mythoscircle-api-1 mythoscircle-api:local" in commands
    assert not (tmp_path / "state/deployed-revision").exists()
    assert (tmp_path / "state/failed-revision").read_text().strip() == REVISION
    assert "Previous release restored" in result.stderr


def test_failed_build_keeps_service_running_and_suppresses_retry(tmp_path: Path) -> None:
    result, commands = run_deploy(tmp_path, "buildfail")
    assert result.returncode != 0
    assert "stop web" not in commands
    assert (tmp_path / "state/failed-revision").read_text().strip() == REVISION
    (tmp_path / "commands.log").unlink()
    result, commands = run_deploy(tmp_path)
    assert result.returncode != 0
    assert not commands


def test_actions_images_are_verified_without_rebuilding(tmp_path: Path) -> None:
    result, commands = run_deploy(tmp_path, images_ready=True)
    assert result.returncode == 0, result.stderr
    assert "build" not in commands
    assert commands.count("image inspect") == 2
    assert (tmp_path / "state/deployed-revision").read_text().strip() == REVISION


def test_wrong_release_image_does_not_interrupt_service(tmp_path: Path) -> None:
    result, commands = run_deploy(tmp_path, "wrongimage", images_ready=True)
    assert result.returncode != 0
    assert "does not match" in result.stderr
    assert "stop web" not in commands
    assert not (tmp_path / "state/deployed-revision").exists()
