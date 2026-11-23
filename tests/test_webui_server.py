"""Tests for the unified Web UI server (KB + template layer, one port)."""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path
from typing import Any

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import webui_server as wui  # noqa: E402

FX = "FX-T268-V6-10米-5065.NET"


@pytest.fixture(scope="module")
def base_url() -> str:
    from circuit_graph.knowledge.db import ComponentDatabase

    db = ComponentDatabase.default()
    wui.KBAPIHandler.db = db
    wui.TemplateAPIHandler.db = db

    httpd = HTTPServer(("127.0.0.1", 0), wui.WebUIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _get(url: str) -> tuple[int, dict[str, str], bytes]:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def _post_json(url: str, payload: dict[str, Any]) -> tuple[int, dict[str, str], bytes]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


def test_index_lists_both_pages(base_url: str) -> None:
    status, _headers, body = _get(f"{base_url}/")
    assert status == 200
    text = body.decode("utf-8")
    assert "/kb" in text
    assert "/tpl" in text


def test_kb_page_served(base_url: str) -> None:
    status, _headers, body = _get(f"{base_url}/kb")
    assert status == 200
    assert "circuit-graph KB Manager" in body.decode("utf-8")


def test_template_page_served(base_url: str) -> None:
    status, _headers, body = _get(f"{base_url}/tpl")
    assert status == 200
    assert "模板" in body.decode("utf-8")


def test_template_api_route(base_url: str) -> None:
    status, _headers, body = _get(f"{base_url}/api/primitives")
    assert status == 200
    data = json.loads(body.decode("utf-8"))
    assert isinstance(data, list)
    assert "jw5065-buck" in {row["id"] for row in data}


def test_kb_api_route(base_url: str) -> None:
    status, _headers, body = _get(f"{base_url}/api/components")
    assert status == 200
    json.loads(body.decode("utf-8"))


def test_unknown_api_path_404(base_url: str) -> None:
    status, _headers, _body = _get(f"{base_url}/api/does-not-exist")
    assert status == 404


def test_no_cors_headers(base_url: str) -> None:
    for path in ("/", "/kb", "/tpl", "/api/primitives", "/api/components"):
        status, headers, _body = _get(f"{base_url}{path}")
        assert status == 200
        assert "access-control-allow-origin" not in {k.lower() for k in headers}
        assert not any(k.lower() == "access-control-allow-origin" for k in headers)


def test_post_draft_dispatch(base_url: str) -> None:
    payload = {
        "board": FX,
        "chip": "U4",
        "function": "JW5065 bootstrap draft test",
        "id": "draft-webui-test",
        "chip_slot": {"member_id": "dcdc", "required": True},
        "slots": [
            {
                "designator": "C70",
                "member_id": "C_bst",
                "title": "自举电容",
                "value_type": "cap_nf",
            }
        ],
        "shared_signals": [],
    }
    status, headers, body = _post_json(f"{base_url}/api/draft", payload)
    assert status == 200
    assert not any(k.lower() == "access-control-allow-origin" for k in headers)
    data = json.loads(body.decode("utf-8"))
    comp_ids = {c["id"] for c in data["draft"]["components"]}
    assert {"dcdc", "C_bst"} <= comp_ids
