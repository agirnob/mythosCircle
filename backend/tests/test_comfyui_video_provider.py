"""ComfyUI video provider tests (spec-4.5): the poll-based
submit -> poll /history -> fetch /view i2v adapter — deterministic, the
httpx transport is injected (MockTransport).

Mirrors the 4-4 image adapter's contract tests (test_comfyui_image_provider.py):
healthy decode, bearer key, config swappability, http/connection/timeout
error classes and malformed-payload rejection — plus the video-specific
rows: the reveal prompt injected into the MiniMax node's
``inputs.prompt`` (NOT ``inputs.value``), the entity's portrait staged
into ComfyUI's input dir and wired to the LoadImage node's
``inputs.image`` (with no litter after the call), the mp4 flow, and the
first-frame failure modes (matrix rows FIRST_FRAME_STAGE_FAIL,
NO_FIRST_FRAME).
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.settings import ComfyUIVideoSettings
from app.providers.comfyui_video import comfyui_video_generation
from app.providers.llm import ProviderError

#: A minimal plausible mp4: an ISO-BMFF box whose size+type occupy bytes
#: 0-7 with the ``ftyp`` box at bytes 4-8 (the provider's /view guard).
MP4 = b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00reveal-payload"

#: The operator-workflow shape the tests pin: a MiniMax prompt-widget
#: node (``inputs.prompt``), a LoadImage node (``inputs.image``) and a
#: SaveVideo output node (node ids match the config defaults).
WORKFLOW: dict[str, Any] = {
    "105:104": {
        "class_type": "MiniMaxH3ImageToVideo",
        "inputs": {
            "prompt": "stale prompt",
            "first_frame": ["114", 0],
            "width": ["115", 0],
        },
    },
    "114": {"class_type": "LoadImage", "inputs": {"image": "stale.png [output]"}},
    "92": {"class_type": "SaveVideo", "inputs": {}},
}


def _write_workflow(path: Path, workflow: dict[str, Any] | str = WORKFLOW) -> Path:
    body = json.dumps(workflow) if not isinstance(workflow, str) else workflow
    path.write_text(body)
    return path


def _settings(workflow_path: Path, input_dir: Path, **overrides: Any) -> ComfyUIVideoSettings:
    return ComfyUIVideoSettings(
        endpoint="http://127.0.0.1:7896",
        workflow_path=str(workflow_path),
        input_dir=str(input_dir),
        **overrides,
    )


def _portrait(parent: Path, name: str = "portrait.png") -> Path:
    """A source-frame portrait on disk (the file run_video would pass as
    ``first_frame`` — the provider stages a copy of these bytes)."""
    path = parent / name
    path.write_bytes(b"\x89PNG\r\n\x1a\nportrait-payload")
    return path


def _history_body() -> dict[str, Any]:
    """A completed ComfyUI history entry mirroring the REAL
    VideoHelperSuite SaveVideo output shape: the ``gifs`` list leads
    with an animated-GIF preview artifact, with the actual mp4 clip
    after it (review round 1 — the provider must PREFER the .mp4 entry,
    not the first file-carrying one)."""
    return {
        "p-1": {
            "outputs": {
                "92": {
                    "gifs": [
                        {
                            "filename": "MiniMax_H3_00001_.gif",
                            "subfolder": "video",
                            "type": "output",
                            "format": "image/gif",
                        },
                        {
                            "filename": "MiniMax_H3_00001_.mp4",
                            "subfolder": "video",
                            "type": "output",
                            "format": "video/h264-mp4",
                        },
                    ]
                }
            }
        }
    }


def test_comfyui_video_prefers_mp4_output_over_gif_preview(tmp_path: Path) -> None:
    """A SaveVideo run that reports a GIF preview BEFORE the clip must
    still yield the mp4: the provider prefers a ``.mp4``-suffixed entry
    (the ISO-BMFF clip it fetches from /view) over the first file-
    carrying artifact — fetching the gif would return non-mp4 bytes and
    fail a valid job (review round 1)."""
    input_dir = tmp_path / "comfy-input"
    view_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        assert request.url.path == "/view"
        view_params.update(request.url.params)
        return httpx.Response(200, content=MP4)

    comfyui_video_generation(
        "the reveal",
        settings=_settings(_write_workflow(tmp_path / "minimax.json"), input_dir),
        first_frame=str(_portrait(tmp_path)),
        transport=httpx.MockTransport(handler),
    )
    assert view_params["filename"] == "MiniMax_H3_00001_.mp4"


def test_comfyui_video_generation_returns_mp4_bytes(tmp_path: Path) -> None:
    """A healthy submit -> poll -> fetch cycle yields the mp4 bytes: the
    ONLY workflow mutations are the MiniMax prompt node's inputs.prompt
    and the LoadImage node's inputs.image (the staged copy's name); the
    rest of the operator's JSON rides through untouched and the file on
    disk is never rewritten (deep copy). The staged portrait exists in
    ComfyUI's input dir DURING the request and is gone AFTER it."""
    workflow_path = _write_workflow(tmp_path / "minimax.json")
    input_dir = tmp_path / "comfy-input"
    portrait = _portrait(tmp_path)
    submitted: dict[str, Any] = {}
    staged_during_call: list[str] = []
    view_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            submitted["body"] = json.loads(request.read().decode())
            staged_during_call.extend(p.name for p in input_dir.iterdir())
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        assert request.url.path == "/view"
        view_params.update(request.url.params)
        return httpx.Response(200, content=MP4)

    data = comfyui_video_generation(
        "the reveal",
        settings=_settings(workflow_path, input_dir),
        first_frame=str(portrait),
        transport=httpx.MockTransport(handler),
    )
    assert data == MP4
    prompt_node = submitted["body"]["prompt"]["105:104"]
    assert prompt_node["inputs"]["prompt"] == "the reveal"
    # The MiniMax node's other inputs ride through untouched.
    assert prompt_node["inputs"]["first_frame"] == ["114", 0]
    assert prompt_node["inputs"]["width"] == ["115", 0]
    image_name = submitted["body"]["prompt"]["114"]["inputs"]["image"]
    assert image_name.startswith("first_frame_") and image_name.endswith(".png")
    # Exactly the one staged copy existed during the request, and the
    # input dir is empty after — no litter (spec-4.5 Always list).
    assert staged_during_call == [image_name]
    assert list(input_dir.iterdir()) == []
    assert view_params == {
        "filename": "MiniMax_H3_00001_.mp4",
        "subfolder": "video",
        "type": "output",
    }
    # The provider deep-copies before mutating — the file still carries
    # the operator's stale prompt.
    assert json.loads(workflow_path.read_text())["105:104"]["inputs"]["prompt"] == "stale prompt"


def test_comfyui_video_workflow_reloaded_from_disk_on_every_call(tmp_path: Path) -> None:
    """No state caching across calls (spec-4.5 Never list): a workflow
    edited between calls is exactly what the next call submits."""
    workflow_path = _write_workflow(tmp_path / "minimax.json")
    input_dir = tmp_path / "comfy-input"
    portrait = _portrait(tmp_path)
    submitted_values: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            submitted_values.append(
                json.loads(request.read().decode())["prompt"]["105:104"]["inputs"]["prompt"]
            )
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=MP4)

    transport = httpx.MockTransport(handler)
    comfyui_video_generation(
        "first",
        settings=_settings(workflow_path, input_dir),
        first_frame=str(portrait),
        transport=transport,
    )
    _write_workflow(
        workflow_path,
        {
            "105:104": {
                "class_type": "MiniMaxH3ImageToVideo",
                "inputs": {"prompt": "edited"},
            },
            "114": {"class_type": "LoadImage", "inputs": {"image": "stale.png"}},
        },
    )
    comfyui_video_generation(
        "second",
        settings=_settings(workflow_path, input_dir),
        first_frame=str(portrait),
        transport=transport,
    )
    # If the provider cached the workflow, the second call would re-submit
    # the first prompt — the injected value proves the fresh disk read.
    assert submitted_values == ["first", "second"]


def test_comfyui_video_sends_bearer_key_when_configured(tmp_path: Path) -> None:
    seen: dict[str, str] = {}
    input_dir = tmp_path / "comfy-input"
    portrait = _portrait(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request.headers.get("authorization", "")
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=MP4)

    comfyui_video_generation(
        "x",
        settings=_settings(_write_workflow(tmp_path / "k.json"), input_dir, api_key="comfy-secret"),
        first_frame=str(portrait),
        transport=httpx.MockTransport(handler),
    )
    assert seen == {
        "/prompt": "Bearer comfy-secret",
        "/history/p-1": "Bearer comfy-secret",
        "/view": "Bearer comfy-secret",
    }


def test_comfyui_video_no_key_header_when_unset(tmp_path: Path) -> None:
    seen: dict[str, str] = {}
    input_dir = tmp_path / "comfy-input"
    portrait = _portrait(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request.headers.get("authorization", "")
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=MP4)

    comfyui_video_generation(
        "x",
        settings=_settings(_write_workflow(tmp_path / "k.json"), input_dir),
        first_frame=str(portrait),
        transport=httpx.MockTransport(handler),
    )
    assert seen == {"/prompt": "", "/history/p-1": "", "/view": ""}


def test_comfyui_video_nodes_are_config_swappable(tmp_path: Path) -> None:
    """A different ComfyUI host, MiniMax prompt node, and LoadImage node
    are a config change, never a code change."""
    workflow = {
        "5": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"prompt": "stale"}},
        "7": {"class_type": "LoadImage", "inputs": {"image": "stale.png"}},
    }
    path = _write_workflow(tmp_path / "k.json", workflow)
    input_dir = tmp_path / "comfy-input"
    portrait = _portrait(tmp_path)
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=MP4)

    comfyui_video_generation(
        "x",
        settings=ComfyUIVideoSettings(
            endpoint="http://comfy.internal:8999",
            workflow_path=str(path),
            prompt_node_id="5",
            first_frame_node_id="7",
            input_dir=str(input_dir),
        ),
        first_frame=str(portrait),
        transport=httpx.MockTransport(handler),
    )
    assert captured["url"] == "http://comfy.internal:8999/prompt"
    assert captured["body"]["prompt"]["5"]["inputs"]["prompt"] == "x"
    assert captured["body"]["prompt"]["7"]["inputs"]["image"].startswith("first_frame_")
    # No /v1 mount is appended: ComfyUI's API is bare-host.
    assert not captured["url"].endswith("/v1/prompt")


def test_comfyui_video_submit_http_error_raises_provider_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="comfyui exploded")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 500


def test_comfyui_video_submit_connection_error_raises_provider_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"


def test_comfyui_video_poll_timeout_raises_timeout_error(tmp_path: Path) -> None:
    """POLL_TIMEOUT: /history never produces a SaveVideo output before
    the timeout elapses — poll exhaustion — and the staged portrait is
    still cleaned up (no litter on a failed call)."""
    input_dir = tmp_path / "comfy-input"
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        calls.append(request.url.path)
        return httpx.Response(200, json={})  # still running, forever

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), input_dir, timeout=1.0),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "timeout"
    assert all(call == "/history/p-1" for call in calls)
    assert list(input_dir.iterdir()) == []


def test_comfyui_video_malformed_submit_non_json_raises_provider_error(
    tmp_path: Path,
) -> None:
    """MALFORMED_SUBMIT: a 200 with a non-JSON body is a provider error,
    never a raw JSONDecodeError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>bad gateway</html>")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_video_malformed_submit_no_prompt_id_raises_provider_error(
    tmp_path: Path,
) -> None:
    """MALFORMED_SUBMIT: a 200 JSON body without prompt_id is a provider
    error — never poll a phantom prompt."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_video_no_video_output_raises_provider_error(tmp_path: Path) -> None:
    """NO_VIDEO_OUTPUT: the prompt finished but no output node produced a
    video file — fail immediately, never poll forever."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        return httpx.Response(200, json={"p-1": {"outputs": {"12": {"images": []}}}})

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_video_history_http_error_raises_provider_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        return httpx.Response(500, text="history exploded")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 500


def test_comfyui_video_history_non_json_200_raises_provider_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        return httpx.Response(200, text="not json")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_video_view_fetch_fail_raises_provider_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(500, text="view exploded")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 500


def test_comfyui_video_non_mp4_view_bytes_rejected_provider_side(tmp_path: Path) -> None:
    """NON_MP4_BYTES: a /view 200 that wraps garbage (an HTML error page,
    a webm, a truncated body) is rejected PROVIDER-side — run_video
    never sees it (matrix row; the write-boundary guard stays single)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=b"<html>not a video</html>")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_video_empty_view_bytes_rejected(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=b"")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_video_missing_workflow_path_raises_connection_error(tmp_path: Path) -> None:
    """WORKFLOW_PATH_MISSING: the configured path doesn't exist — an
    operator misconfig surfaced as a connection-class failure at first
    call (never a raw FileNotFoundError), before any staging or HTTP."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request may happen without a workflow")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(tmp_path / "nope.json", tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"


def test_comfyui_video_malformed_workflow_json_raises_connection_error(
    tmp_path: Path,
) -> None:
    """INVALID_WORKFLOW_JSON: the file exists but is not JSON — a
    connection-class failure, never a raw JSONDecodeError."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request may happen without a valid workflow")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(
                _write_workflow(tmp_path / "k.json", "not json at all"), tmp_path / "in"
            ),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"


def test_comfyui_video_workflow_without_prompt_widget_raises_connection(
    tmp_path: Path,
) -> None:
    """A prompt node whose inputs lack a string ``prompt`` is NOT the
    MiniMax shape (its widget is ``inputs.prompt``, not ``inputs.value``)
    — operator misconfig surfaced at the call site instead of submitting
    a workflow with a phantom key."""
    workflow = {"105:104": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"value": "x"}}}

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request may happen without a valid workflow")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json", workflow), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"


def test_comfyui_video_prompt_node_non_dict_inputs_raises_connection(
    tmp_path: Path,
) -> None:
    """Present-but-NON-DICT ``inputs`` on the prompt node (a foreign or
    malformed workflow) must be a connection-class failure, never a raw
    AttributeError escaping the provider (review round 1)."""
    workflow = {
        "105:104": {"class_type": "MiniMaxH3ImageToVideo", "inputs": ["not", "a", "dict"]},
        "114": {"class_type": "LoadImage", "inputs": {"image": "stale.png"}},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request may happen without a valid workflow")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json", workflow), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"


def test_comfyui_video_frame_node_non_dict_inputs_raises_connection(
    tmp_path: Path,
) -> None:
    """The same guard on the first-frame (LoadImage) node (review
    round 1)."""
    workflow = {
        "105:104": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"prompt": "x"}},
        "114": {"class_type": "LoadImage", "inputs": ["not", "a", "dict"]},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request may happen without a valid workflow")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json", workflow), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"


def test_comfyui_video_partial_stage_copy_is_cleaned_up(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FIRST_FRAME_STAGE_FAIL mid-copy: a copyfile failure after the
    staged path was created leaves NO partial file in ComfyUI's input
    dir — the provider unlinks what it started (review round 1)."""
    import shutil

    input_dir = tmp_path / "comfy-input"

    def exploding_copyfile(src: Any, dst: Any) -> None:
        # The destination file already exists (created by mkdir+open
        # semantics of copyfile) — simulate a disk-full mid-transfer.
        Path(dst).write_bytes(b"partial")
        raise OSError("No space left on device")

    monkeypatch.setattr(shutil, "copyfile", exploding_copyfile)

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request may happen without a staged first frame")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), input_dir),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"
    assert list(input_dir.iterdir()) == []  # no partial litter


def test_comfyui_video_workflow_without_load_image_node_raises_connection(
    tmp_path: Path,
) -> None:
    """A workflow whose first-frame node lacks a string ``inputs.image``
    cannot carry the staged portrait — misconfig, not a submit."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request may happen without a valid workflow")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(
                _write_workflow(
                    tmp_path / "k.json",
                    {"105:104": {"class_type": "MiniMaxH3ImageToVideo", "inputs": {"prompt": "x"}}},
                ),
                tmp_path / "in",
            ),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"


def test_comfyui_video_hostile_prompt_id_rejected(tmp_path: Path) -> None:
    """A /prompt 200 whose prompt_id can inject path segments (e.g.
    "a/b") is a provider error — the id is validated BEFORE URL
    interpolation into /history."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "a/b"})
        raise AssertionError("no history/view request may follow an unvalidated prompt_id")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_video_missing_first_frame_file_raises_connection(tmp_path: Path) -> None:
    """FIRST_FRAME_STAGE_FAIL: the portrait's manifest row exists but its
    FILE is gone from disk — a connection-class provider failure, never
    a raw exception, and no HTTP request happens (fail before submit)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request may happen without a stageable first frame")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            first_frame=str(tmp_path / "missing.png"),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"
    assert not (tmp_path / "in").exists()  # nothing staged


def test_comfyui_video_no_first_frame_raises_connection(tmp_path: Path) -> None:
    """NO_FIRST_FRAME (provider-side twin of the run_video gate): calling
    without a source frame is a provider error — i2v cannot render
    without one."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request may happen without a first frame")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"


def test_comfyui_video_unset_input_dir_raises_connection(tmp_path: Path) -> None:
    """FIRST_FRAME_STAGE_FAIL: an unset ComfyUI input dir is operator
    misconfig — the provider must not stage into the process CWD."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request may happen without an input dir")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=ComfyUIVideoSettings(
                endpoint="http://127.0.0.1:7896",
                workflow_path=str(_write_workflow(tmp_path / "k.json")),
                input_dir="",
            ),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"


def test_comfyui_video_truncated_mp4_rejected(tmp_path: Path) -> None:
    """A /view 200 whose body has an intact start but is too short to
    carry the ftyp box is rejected provider-side."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=b"\x00\x00\x00ft")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_video_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), tmp_path / "in"),
            first_frame=str(_portrait(tmp_path)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_and_media_mp4_guards_are_twin() -> None:
    """The provider-side mp4 magic and run_video's write-boundary guard
    are the SAME constant — they can never silently drift apart (the 4-4
    PNG-twin contract)."""
    from app.media.service import MP4_SIGNATURE as media_mp4
    from app.providers.comfyui_video import _MP4_SIGNATURE as provider_mp4

    assert provider_mp4 == media_mp4 == b"ftyp"
