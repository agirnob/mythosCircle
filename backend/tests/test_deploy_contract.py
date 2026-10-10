"""Deploy-contract tests: the app's DB path and the deploy bundle agree.

Pins the composition the review flagged as unverified: the store's default
database URL, ``deploy/config.toml``, and the backup/restore scripts must
all target the same production database file — otherwise a fresh
deployment silently never backs up the file the app actually writes
(Story 1.7 wiring, pinned now so drift fails loudly).
"""

import json
import os
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
    """backup.sh and restore.sh resolve their data dir from the same
    default the operator config declares; restore's DB filename comes
    from the snapshot manifest (db.file), which backup.py writes at
    snapshot time — the pair stays on one file without restore
    hardcoding a name (6-2: faithful to the snapshot, dev DB
    mythos.db included)."""
    config = tomllib.loads((DEPLOY_DIR / "config.toml").read_text())
    data_dir = config["world"]["data_dir"]
    backup_text = (DEPLOY_DIR / "backup.sh").read_text()
    restore_text = (DEPLOY_DIR / "restore.sh").read_text()
    for text in (backup_text, restore_text):
        assert f'DATA_DIR="${{MYTHOSCIRCLE_DATA_DIR:-{data_dir}}}"' in text
    assert 'DB="$DATA_DIR/mythoscircle.db"' in backup_text
    # 6-2: restore delegates the DB name to the manifest — never a
    # hardcoded filename here.
    assert 'DB="$DATA_DIR/mythoscircle.db"' not in restore_text
    assert "-m app.core.restore" in restore_text


def test_api_binds_loopback_only() -> None:
    """AR29/AR2: the API binds loopback-only; TLS terminates at the
    Cloudflare edge and Caddy is a plain-HTTP tunnel origin (CGNAT: no
    inbound 80/443, no ACME). Both the operator config and the systemd
    unit pin 127.0.0.1 — a public bind would expose auth over
    cleartext. base_url stays the https edge origin (minted portrait
    URLs point at it)."""
    config = tomllib.loads((DEPLOY_DIR / "config.toml").read_text())
    assert config["server"]["host"] == "127.0.0.1"
    assert config["server"]["base_url"] == "https://world.miscco.uk"
    service = (DEPLOY_DIR / "mythoscircle.service").read_text()
    assert "--host 127.0.0.1" in service
    caddy = (DEPLOY_DIR / "Caddyfile").read_text()
    assert "http://world.miscco.uk" in caddy
    assert "reverse_proxy" in caddy.lower()


def test_both_compose_variants_set_cookie_secure() -> None:
    """Spec-6-4 (AR14): both repo compose variants declare
    MYTHOSCIRCLE_COOKIE_SECURE=true in the api service env — the origin
    behind the TLS edge serves plain HTTP and never learns the public
    scheme, so the Secure flag is operator-declared (AD-22). Unset keeps
    the dev plain-http flow non-Secure; the auth-api pins cover the
    tri-state."""
    for compose_name in ("docker-compose.yml", "docker-compose.portainer.yml"):
        text = (DEPLOY_DIR / compose_name).read_text()
        # Anchor on the top-level service keys (both files declare api
        # before web): isolate exactly the api service block.
        api_block = text.split("\n  api:", 1)[1].split("\n  web:", 1)[0]
        assert 'MYTHOSCIRCLE_COOKIE_SECURE: "true"' in api_block, compose_name


def test_image_and_video_backend_and_comfyui_contract() -> None:
    """Spec-4.4/4.5: the shipped [image]/[video] backends stay openai
    (default — a flipped backend would silently change production
    routing), and the [comfyui_image]/[comfyui_video] sections point at
    the workflows SHIPPED in deploy/workflows/ via a REPO-RELATIVE path
    (machine-independent across checkouts — the config file's dir is
    deploy/, so ``workflows/…json`` resolves next to it; review round
    1) with their documented node ids and whole-call timeout. Pinned so
    drift fails loudly."""
    config = tomllib.loads((DEPLOY_DIR / "config.toml").read_text())
    assert config["image"]["backend"] == "openai"
    assert config["video"]["backend"] == "openai"
    comfyui_image = config["comfyui_image"]
    assert comfyui_image["endpoint"] == "http://127.0.0.1:7896"
    assert comfyui_image["workflow_path"] == "workflows/image_krea2_turbo_t2i_int8 (2).json"
    assert not os.path.isabs(comfyui_image["workflow_path"])
    assert (DEPLOY_DIR / comfyui_image["workflow_path"]).is_file()
    assert comfyui_image["prompt_node_id"] == "30:19"
    assert comfyui_image["aspect_ratio"] == "1:1 (Square)"
    assert comfyui_image["megapixels"] == 1.0
    assert comfyui_image["timeout"] == 1800
    comfyui_video = config["comfyui_video"]
    assert comfyui_video["endpoint"] == "http://127.0.0.1:7896"
    assert comfyui_video["workflow_path"] == "workflows/video_minimax_h3_i2v_sage.json"
    assert not os.path.isabs(comfyui_video["workflow_path"])
    assert (DEPLOY_DIR / comfyui_video["workflow_path"]).is_file()
    assert comfyui_video["prompt_node_id"] == "105:104"
    assert comfyui_video["first_frame_node_id"] == "114"
    assert comfyui_video["input_dir"] == ""  # operator must set (ComfyUI install)
    assert comfyui_video["timeout"] == 1800


def test_shipped_comfyui_workflows_parse_and_carry_provider_shape() -> None:
    """Spec-4.5 repo-home: every configured workflow JSON ships in
    deploy/workflows/, parses, and carries EXACTLY the node/widget shape
    the providers inject into — a node renumbering or widget rename in
    an operator edit fails AT COMMIT TIME instead of surfacing as a
    first-call ProviderError('connection') (review round 1)."""
    config = tomllib.loads((DEPLOY_DIR / "config.toml").read_text())

    image_wf = json.loads((DEPLOY_DIR / config["comfyui_image"]["workflow_path"]).read_text())
    image_prompt = image_wf[config["comfyui_image"]["prompt_node_id"]]
    assert isinstance(image_prompt["inputs"]["value"], str)  # "Text String (User Prompt)"
    save_image = [n for n in image_wf.values() if n.get("class_type") == "SaveImage"]
    assert save_image, "the image workflow must carry a SaveImage node"

    video_wf = json.loads((DEPLOY_DIR / config["comfyui_video"]["workflow_path"]).read_text())
    video_prompt = video_wf[config["comfyui_video"]["prompt_node_id"]]
    assert isinstance(video_prompt["inputs"]["prompt"], str)  # MiniMax node, NOT inputs.value
    frame = video_wf[config["comfyui_video"]["first_frame_node_id"]]
    assert frame["class_type"] == "LoadImage"
    assert isinstance(frame["inputs"]["image"], str)
    save_video = [n for n in video_wf.values() if n.get("class_type") == "SaveVideo"]
    assert save_video, "the video workflow must carry a SaveVideo node"


def test_video_prompt_writing_guide_ships_and_is_readable() -> None:
    """Spec-4.6: the MiniMax-H3 writing guide ships in-repo at
    deploy/guides/ and is non-empty — the draft runner loads it into the
    generation instruction, so a missing/empty guide would surface as a
    runtime 'guide unreadable' job failure instead of a deploy-time
    defect. Also pins the I2VA contract the drafts must follow."""
    guide = DEPLOY_DIR / "guides" / "VIDEO_PROMPT_WRITING_GUIDE_base_en.md"
    text = guide.read_text(encoding="utf-8")
    assert text.strip(), "the video prompt writing guide must be non-empty"
    # The three core fields the I2VA drafts must carry (spec-4.6
    # acceptance: a structured draft contains all three).
    for field in (
        "integrated_multimodal_description",
        "overall_soundscape",
        "non_diegetic_music",
    ):
        assert field in text, f"the writing guide must document {field!r}"
    # The I2VA picture-alignment instruction the draft MUST open with.
    assert "is fully referenced" in text


def test_admin_allowlist_forwarded_only_to_api_with_empty_default() -> None:
    for name in ("docker-compose.yml", "docker-compose.portainer.yml"):
        compose = (DEPLOY_DIR / name).read_text()
        api, web = compose.split("\n  api:", 1)[1].split("\n  web:", 1)
        assert "MYTHOSCIRCLE_ADMIN_ACCOUNT_IDS: ${MYTHOSCIRCLE_ADMIN_ACCOUNT_IDS:-}" in api
        assert "MYTHOSCIRCLE_ADMIN_ACCOUNT_IDS" not in web
    assert "MYTHOSCIRCLE_ADMIN_ACCOUNT_IDS" not in (DEPLOY_DIR / "config.toml").read_text()
    instructions = (DEPLOY_DIR / "AUTO_DEPLOY.md").read_text()
    assert "/home/homest/mythos-redeploy.yml" in instructions
    assert "runner does not automatically inherit" in instructions
