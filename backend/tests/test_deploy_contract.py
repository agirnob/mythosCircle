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
