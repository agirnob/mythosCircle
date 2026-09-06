"""Deploy-contract tests: the app's DB path and the deploy bundle agree.

Pins the composition the review flagged as unverified: the store's default
database URL, ``deploy/config.toml``, and the backup/restore scripts must
all target the same production database file — otherwise a fresh
deployment silently never backs up the file the app actually writes
(Story 1.7 wiring, pinned now so drift fails loudly).
"""

import tomllib
from pathlib import Path

from app.store import db

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_DIR = REPO_ROOT / "deploy"


def test_default_db_url_matches_config_toml() -> None:
    """The store's default URL points at exactly the path the operator
    config declares (a CWD-independent absolute path)."""
    config = tomllib.loads((DEPLOY_DIR / "config.toml").read_text())
    data_dir = config["world"]["data_dir"]
    sqlite_path = config["world"]["sqlite_path"]
    assert sqlite_path == f"{data_dir}/mythoscircle.db"
    assert f"sqlite:///{sqlite_path}" == db.DEFAULT_DB_URL


def test_backup_and_restore_target_the_same_db() -> None:
    """backup.sh and restore.sh resolve their DB path from the same
    data-dir default the operator config declares."""
    config = tomllib.loads((DEPLOY_DIR / "config.toml").read_text())
    data_dir = config["world"]["data_dir"]
    for script in ("backup.sh", "restore.sh"):
        text = (DEPLOY_DIR / script).read_text()
        assert f'DATA_DIR="${{MYTHOSCIRCLE_DATA_DIR:-{data_dir}}}"' in text
        assert 'DB="$DATA_DIR/mythoscircle.db"' in text


def test_api_binds_loopback_only() -> None:
    """AR29/AR2: the API binds loopback-only; Caddy fronts TLS. Both the
    operator config and the systemd unit pin 127.0.0.1 — a public bind
    would expose auth over cleartext."""
    config = tomllib.loads((DEPLOY_DIR / "config.toml").read_text())
    assert config["server"]["host"] == "127.0.0.1"
    service = (DEPLOY_DIR / "mythoscircle.service").read_text()
    assert "--host 127.0.0.1" in service
    caddy = (DEPLOY_DIR / "Caddyfile").read_text()
    assert "tls" in caddy.lower()
    assert "reverse_proxy" in caddy.lower()


def test_image_backend_and_comfyui_placeholder_contract() -> None:
    """Spec-4.4: the shipped [image] backend stays openai (default — a
    flipped backend would silently change production portrait routing)
    and the [comfyui_image] placeholders keep their documented shapes
    (empty workflow_path until the operator fills it, the Krea2 prompt
    node, whole-call timeout). Pinned so drift fails loudly (review
    round 1)."""
    config = tomllib.loads((DEPLOY_DIR / "config.toml").read_text())
    assert config["image"]["backend"] == "openai"
    comfyui_image = config["comfyui_image"]
    assert comfyui_image["endpoint"] == "http://127.0.0.1:7896"
    assert comfyui_image["workflow_path"] == ""
    assert comfyui_image["prompt_node_id"] == "30:28"
    assert comfyui_image["aspect_ratio"] == "1:1 (Square)"
    assert comfyui_image["megapixels"] == 1.0
    assert comfyui_image["timeout"] == 1800
