"""ComfyUI image provider tests (spec-4.4): the poll-based
submit -> poll /history -> fetch /view adapter — deterministic, the
httpx transport is injected (MockTransport).

Mirrors the OpenAI adapter's contract tests (test_image_provider.py):
healthy decode, bearer key, config swappability, http/connection error
classes and malformed-payload rejection — plus the ComfyUI-specific
rows: poll exhaustion (the only "timeout" kind caller), the workflow
file failure modes, and disk-reload-per-call.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.core.settings import ComfyUIImageSettings
from app.providers.comfyui import comfyui_image_generation
from app.providers.llm import ProviderError

#: A minimal complete PNG (signature + one empty chunk + the IEND chunk
#: with its fixed CRC): the provider requires the IEND terminator, so
#: every happy-path fixture must be a COMPLETE png, not just a signature.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x00" + b"IEND\xaeB`\x82"

#: The operator-workflow shape the tests pin: a prompt widget node and a
#: SaveImage node (the prompt node id matches the config default).
WORKFLOW: dict[str, Any] = {
    "30:28": {"class_type": "CLIPTextEncode", "inputs": {"value": "stale prompt"}},
    "29": {"class_type": "SaveImage", "inputs": {}},
}


def _write_workflow(path: Path, workflow: dict[str, Any] | str = WORKFLOW) -> Path:
    body = json.dumps(workflow) if not isinstance(workflow, str) else workflow
    path.write_text(body)
    return path


def _settings(workflow_path: Path, **overrides: Any) -> ComfyUIImageSettings:
    return ComfyUIImageSettings(
        endpoint="http://127.0.0.1:7896",
        workflow_path=str(workflow_path),
        **overrides,
    )


def _history_body() -> dict[str, Any]:
    """A completed ComfyUI history entry: node "29" (SaveImage) produced
    one output image."""
    return {
        "p-1": {
            "outputs": {
                "29": {
                    "images": [{"filename": "Krea2_00001_.png", "subfolder": "", "type": "output"}]
                }
            }
        }
    }


def test_comfyui_image_generation_returns_png_bytes(tmp_path: Path) -> None:
    """A healthy submit -> poll -> fetch cycle yields the PNG bytes, and
    the ONLY workflow mutation is the prompt node's inputs.value — the
    rest of the operator's JSON rides through untouched, and the file
    on disk is never rewritten (deep copy)."""
    workflow_path = _write_workflow(tmp_path / "krea2.json")
    submitted: dict[str, Any] = {}
    view_params: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            assert request.method == "POST"
            submitted["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        assert request.url.path == "/view"
        view_params.update(request.url.params)
        return httpx.Response(200, content=PNG)

    data = comfyui_image_generation(
        "a sharp face",
        settings=_settings(workflow_path),
        transport=httpx.MockTransport(handler),
    )
    assert data == PNG
    assert submitted["body"] == {
        "prompt": {
            "30:28": {"class_type": "CLIPTextEncode", "inputs": {"value": "a sharp face"}},
            "29": {"class_type": "SaveImage", "inputs": {}},
        }
    }
    assert view_params == {"filename": "Krea2_00001_.png", "type": "output", "subfolder": ""}
    # The provider deep-copies before mutating — the file still carries
    # the operator's stale prompt.
    assert json.loads(workflow_path.read_text())["30:28"]["inputs"]["value"] == "stale prompt"


def test_comfyui_workflow_reloaded_from_disk_on_every_call(tmp_path: Path) -> None:
    """No state caching across calls (spec-4.4 Never list): a workflow
    edited between calls is exactly what the next call submits — a stale
    workflow must never be served."""
    workflow_path = _write_workflow(tmp_path / "krea2.json")
    submitted_values: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            submitted_values.append(
                json.loads(request.read().decode())["prompt"]["30:28"]["inputs"]["value"]
            )
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=PNG)

    transport = httpx.MockTransport(handler)
    comfyui_image_generation("first", settings=_settings(workflow_path), transport=transport)
    _write_workflow(
        workflow_path,
        {"30:28": {"class_type": "CLIPTextEncode", "inputs": {"value": "edited prompt"}}},
    )
    comfyui_image_generation("second", settings=_settings(workflow_path), transport=transport)
    # If the provider cached the workflow, the second call would re-submit
    # the first prompt — the injected value proves the fresh disk read.
    assert submitted_values == ["first", "second"]


def test_comfyui_image_sends_bearer_key_when_configured(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request.headers.get("authorization", "")
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=PNG)

    comfyui_image_generation(
        "x",
        settings=_settings(_write_workflow(tmp_path / "k.json"), api_key="comfy-secret"),
        transport=httpx.MockTransport(handler),
    )
    assert seen == {
        "/prompt": "Bearer comfy-secret",
        "/history/p-1": "Bearer comfy-secret",
        "/view": "Bearer comfy-secret",
    }


def test_comfyui_image_no_key_header_when_unset(tmp_path: Path) -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen[request.url.path] = request.headers.get("authorization", "")
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=PNG)

    comfyui_image_generation(
        "x",
        settings=_settings(_write_workflow(tmp_path / "k.json")),
        transport=httpx.MockTransport(handler),
    )
    assert seen == {"/prompt": "", "/history/p-1": "", "/view": ""}


def test_comfyui_image_endpoint_and_prompt_node_are_config_swappable(tmp_path: Path) -> None:
    """A different ComfyUI host or workflow prompt widget is a config
    change, never a code change (the 4-1 config-swappability row)."""
    workflow = {"5": {"class_type": "CLIPTextEncode", "inputs": {"value": "stale"}}}
    path = _write_workflow(tmp_path / "k.json", workflow)
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=PNG)

    comfyui_image_generation(
        "x",
        settings=ComfyUIImageSettings(
            endpoint="http://comfy.internal:8999", workflow_path=str(path), prompt_node_id="5"
        ),
        transport=httpx.MockTransport(handler),
    )
    assert captured["url"] == "http://comfy.internal:8999/prompt"
    assert captured["body"]["prompt"]["5"]["inputs"]["value"] == "x"
    # No /v1 mount is appended: ComfyUI's API is bare-host.
    assert not captured["url"].endswith("/v1/prompt")


def test_comfyui_submit_http_error_raises_provider_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="comfyui exploded")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json")),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 500


def test_comfyui_submit_connection_error_raises_provider_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json")),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"


def test_comfyui_poll_timeout_raises_timeout_error(tmp_path: Path) -> None:
    """POLL_TIMEOUT: /history never produces a SaveImage output before
    the timeout elapses — poll exhaustion — the ONLY caller of the new
    "timeout" kind (spec-4.4)."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        calls.append(request.url.path)
        return httpx.Response(200, json={})  # still running, forever

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json"), timeout=1.0),
            transport=httpx.MockTransport(handler),
        )
    # The deterministic core is the timeout kind (poll exhaustion); the
    # exact poll COUNT is scheduling-dependent (a slow CI pause between
    # the deadline check and the loop exit can yield zero polls), so only
    # the kind and the poll URL shape are asserted (review round 1).
    assert excinfo.value.kind == "timeout"
    assert all(call == "/history/p-1" for call in calls)


def test_comfyui_malformed_submit_non_json_raises_provider_error(tmp_path: Path) -> None:
    """MALFORMED_SUBMIT: a 200 with a non-JSON body is a provider error,
    never a raw JSONDecodeError."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>bad gateway</html>")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json")),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_malformed_submit_no_prompt_id_raises_provider_error(tmp_path: Path) -> None:
    """MALFORMED_SUBMIT: a 200 JSON body without prompt_id is a provider
    error — never poll a phantom prompt."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": True})

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json")),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_missing_output_node_raises_provider_error(tmp_path: Path) -> None:
    """NO_OUTPUT_NODE: the prompt finished but no SaveImage node
    produced an image — fail immediately, never poll forever."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        return httpx.Response(200, json={"p-1": {"outputs": {"12": {"images": []}}}})

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json")),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_history_http_error_raises_provider_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        return httpx.Response(500, text="history exploded")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json")),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 500


def test_comfyui_history_non_json_200_raises_provider_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        return httpx.Response(200, text="not json")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json")),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_view_fetch_fail_raises_provider_error(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(500, text="view exploded")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json")),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 500


def test_comfyui_non_png_view_bytes_rejected_provider_side(tmp_path: Path) -> None:
    """NON_PNG_BYTES: a /view 200 that wraps garbage (an HTML error page,
    a truncated body) is rejected PROVIDER-side — run_portrait never
    sees it (matrix row; the write-boundary guard stays single)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=b"<html>not an image</html>")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json")),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_empty_view_bytes_rejected(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=b"")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json")),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_missing_workflow_path_raises_connection_error(tmp_path: Path) -> None:
    """WORKFLOW_PATH_MISSING: the configured path doesn't exist — an
    operator misconfig surfaced as a connection-class failure at first
    call (never a raw FileNotFoundError)."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request may happen without a workflow")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(tmp_path / "nope.json"),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"


def test_comfyui_malformed_workflow_json_raises_connection_error(tmp_path: Path) -> None:
    """INVALID_WORKFLOW_JSON: the file exists but is not JSON — a
    connection-class failure, never a raw JSONDecodeError."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request may happen without a valid workflow")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json", "not json at all")),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"


def test_comfyui_workflow_without_prompt_value_raises_connection(tmp_path: Path) -> None:
    """A prompt node whose inputs lack a string ``value`` is NOT the
    Krea2 shape — operator misconfig surfaced at the call site instead
    of submitting a workflow with a phantom key (review round 1)."""
    workflow = {"30:28": {"class_type": "CLIPTextEncode", "inputs": {"positive": "x"}}}

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP request may happen without a valid workflow")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json", workflow)),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "connection"


def test_comfyui_hostile_prompt_id_rejected(tmp_path: Path) -> None:
    """A /prompt 200 whose prompt_id can inject path segments (e.g.
    "a/b") is a provider error — the id is validated BEFORE URL
    interpolation into /history (review round 1)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "a/b"})
        raise AssertionError("no history/view request may follow an unvalidated prompt_id")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json")),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_resolution_selector_receives_configured_shape(tmp_path: Path) -> None:
    """The workflow's ResolutionSelector node is pinned from config:
    its inputs.aspect_ratio / inputs.megapixels carry the configured
    values — one job = one image, fixed shape per config, no per-job
    knobs (review round 1)."""
    workflow = {
        "30:28": {"class_type": "CLIPTextEncode", "inputs": {"value": "stale"}},
        "12": {
            "class_type": "ResolutionSelector",
            "inputs": {"aspect_ratio": "stale", "megapixels": "stale"},
        },
        "29": {"class_type": "SaveImage", "inputs": {}},
    }
    path = _write_workflow(tmp_path / "k.json", workflow)
    submitted: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            submitted["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=PNG)

    comfyui_image_generation(
        "x",
        settings=_settings(path, aspect_ratio="16:9 (Wide)", megapixels=1.5),
        transport=httpx.MockTransport(handler),
    )
    resolution = submitted["body"]["prompt"]["12"]["inputs"]
    assert resolution["aspect_ratio"] == "16:9 (Wide)"
    assert resolution["megapixels"] == 1.5


def test_comfyui_resolution_marker_node_receives_configured_shape(tmp_path: Path) -> None:
    """Inputs carrying BOTH aspect_ratio and megapixels keys — the
    marker, whatever the class_type — are pinned the same way, and the
    sibling inputs ride through untouched (review round 1)."""
    workflow = {
        "30:28": {"class_type": "CLIPTextEncode", "inputs": {"value": "stale"}},
        "7": {
            "class_type": "AnythingElse",
            "inputs": {"aspect_ratio": "x", "megapixels": 0, "positive": "keep me"},
        },
    }
    path = _write_workflow(tmp_path / "k.json", workflow)
    submitted: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            submitted["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=PNG)

    comfyui_image_generation(
        "x",
        settings=_settings(path, aspect_ratio="9:16 (Tall)", megapixels=2.0),
        transport=httpx.MockTransport(handler),
    )
    resolution = submitted["body"]["prompt"]["7"]["inputs"]
    assert resolution["aspect_ratio"] == "9:16 (Tall)"
    assert resolution["megapixels"] == 2.0
    assert resolution["positive"] == "keep me"  # sibling inputs untouched


def test_comfyui_workflow_without_resolution_node_unchanged(tmp_path: Path) -> None:
    """A workflow without a resolution node (no ResolutionSelector, no
    aspect_ratio+megapixels marker) is submitted with ONLY the prompt
    mutated — the fixed-shape absence is tolerated (review round 1)."""
    path = _write_workflow(tmp_path / "k.json", WORKFLOW)
    submitted: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            submitted["body"] = json.loads(request.read().decode())
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=PNG)

    comfyui_image_generation("x", settings=_settings(path), transport=httpx.MockTransport(handler))
    prompt = submitted["body"]["prompt"]
    assert set(prompt) == {"30:28", "29"}
    assert prompt["30:28"]["inputs"]["value"] == "x"
    assert "aspect_ratio" not in prompt["30:28"]["inputs"]
    assert "megapixels" not in prompt["30:28"]["inputs"]


def test_comfyui_truncated_png_rejected(tmp_path: Path) -> None:
    """A /view 200 whose body has an intact PNG signature but is
    truncated (no IEND terminator) is rejected provider-side — it would
    otherwise be written as a corrupt .png (review round 1)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/prompt":
            return httpx.Response(200, json={"prompt_id": "p-1"})
        if request.url.path == "/history/p-1":
            return httpx.Response(200, json=_history_body())
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\n" + b"truncated-no-iend")

    with pytest.raises(ProviderError) as excinfo:
        comfyui_image_generation(
            "x",
            settings=_settings(_write_workflow(tmp_path / "k.json")),
            transport=httpx.MockTransport(handler),
        )
    assert excinfo.value.kind == "http"
    assert excinfo.value.status_code == 200


def test_comfyui_and_media_png_guards_are_twin() -> None:
    """The provider-side PNG magic and run_portrait's write-boundary
    guard are the SAME constant — they can never silently drift apart
    (review round 1)."""
    from app.media.service import PNG_SIGNATURE as media_png
    from app.providers.comfyui import _PNG_SIGNATURE as provider_png

    assert provider_png == media_png == b"\x89PNG\r\n\x1a\n"


class _SlowServer:
    """A minimal ComfyUI-protocol server whose chosen handler sleeps past
    the call's timeout.

    MockTransport never applies timeouts to a sleeping handler (the
    dispatch is synchronous in-process), so the whole-call bound has to
    be proven against the REAL httpx transport over loopback.
    """

    def __init__(self, sleep_path: str, sleep_seconds: float = 3.0) -> None:
        self.sleep_path = sleep_path
        self.sleep_seconds = sleep_seconds
        self.png = PNG
        self.server = HTTPServer(("127.0.0.1", 0), self._handler_type())
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def _handler_type(self) -> type[BaseHTTPRequestHandler]:
        sleep_path = self.sleep_path
        sleep_seconds = self.sleep_seconds
        png = self.png

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _json(self, obj: dict[str, Any], code: int = 200) -> None:
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _sleep(self) -> None:
                if self.path.startswith(sleep_path):
                    time.sleep(sleep_seconds)

            def do_POST(self) -> None:
                self._sleep()
                if self.path == "/prompt":
                    self.rfile.read(int(self.headers.get("Content-Length", 0)))
                    self._json({"prompt_id": "p-1"})
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_GET(self) -> None:
                self._sleep()
                if self.path.startswith("/history/p-1"):
                    self._json(
                        {
                            "p-1": {
                                "outputs": {
                                    "29": {
                                        "images": [
                                            {
                                                "filename": "k.png",
                                                "subfolder": "",
                                                "type": "output",
                                            }
                                        ]
                                    }
                                }
                            }
                        }
                    )
                elif self.path.startswith("/view"):
                    self.send_response(200)
                    self.send_header("Content-Type", "image/png")
                    self.send_header("Content-Length", str(len(png)))
                    self.end_headers()
                    self.wfile.write(png)
                else:
                    self.send_response(404)
                    self.end_headers()

        return Handler

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def test_comfyui_slow_prompt_bounded_by_timeout(tmp_path: Path) -> None:
    """The whole-call timeout binds a hung /prompt: a server that sleeps
    past settings.timeout yields ProviderError("timeout") within roughly
    settings.timeout — never a 2-3x overshoot (review round 1)."""
    server = _SlowServer(sleep_path="/prompt")
    try:
        start = time.monotonic()
        with pytest.raises(ProviderError) as excinfo:
            comfyui_image_generation(
                "x",
                settings=ComfyUIImageSettings(
                    endpoint=f"http://127.0.0.1:{server.port}",
                    workflow_path=str(_write_workflow(tmp_path / "k.json")),
                    timeout=1.0,
                ),
            )
        elapsed = time.monotonic() - start
    finally:
        server.close()
    assert excinfo.value.kind == "timeout"
    assert elapsed < 2.5, f"call ran {elapsed:.2f}s — the whole-call bound was not enforced"


def test_comfyui_slow_view_bounded_by_timeout(tmp_path: Path) -> None:
    """The same bound for a hung /view: the fetch is capped by the
    remaining deadline budget, not the full settings timeout again —
    the whole call stays within settings.timeout (review round 1)."""
    server = _SlowServer(sleep_path="/view")
    try:
        start = time.monotonic()
        with pytest.raises(ProviderError) as excinfo:
            comfyui_image_generation(
                "x",
                settings=ComfyUIImageSettings(
                    endpoint=f"http://127.0.0.1:{server.port}",
                    workflow_path=str(_write_workflow(tmp_path / "k.json")),
                    timeout=1.0,
                ),
            )
        elapsed = time.monotonic() - start
    finally:
        server.close()
    assert excinfo.value.kind == "timeout"
    assert elapsed < 2.5, f"call ran {elapsed:.2f}s — the whole-call bound was not enforced"
