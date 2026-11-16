"""Tests for template WebUI lib (no live HTTP server)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPTS = _REPO / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import template_web_lib as twl  # noqa: E402

FX = "FX-T268-V6-10米-5065.NET"


def test_primitive_list_includes_jw5065_with_c_bst() -> None:
    rows = twl.list_primitives_summary()
    row = next(r for r in rows if r["id"] == "jw5065-buck")
    raw = twl.load_primitive_raw("jw5065-buck")
    member_ids = {c["id"] for c in raw.get("components", [])}
    assert "C_bst" in member_ids
    assert row["member_count"] >= 5


def test_path_traversal_rejected() -> None:
    with pytest.raises(twl.TemplateWebError):
        twl.validate_primitive_id("../etc")
    with pytest.raises(twl.TemplateWebError):
        twl.validate_primitive_id("bad_id")
    with pytest.raises(twl.TemplateWebError):
        twl.validate_board_name("../secret.NET")
    with pytest.raises(twl.TemplateWebError):
        twl.resolve_board_path("no-such-board.NET")


def test_round_trip_preserves_unknown_fields(tmp_path: Path) -> None:
    prim = tmp_path / "primitives"
    prim.mkdir()
    (prim / "schema.json").write_text(
        (_REPO / "primitives" / "schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    doc = json.loads(
        ( _REPO / "primitives" / "power" / "jw5065-buck.json").read_text(encoding="utf-8")
    )
    doc["custom_ui_field"] = {"note": "preserve me"}
    rel = "power/test-roundtrip.json"
    twl.save_primitive(
        rel,
        doc,
        enable_write=True,
        primitives=prim,
        board=FX,
    )
    saved = json.loads((prim / rel).read_text(encoding="utf-8"))
    assert saved.get("custom_ui_field") == doc["custom_ui_field"]
    assert twl.round_trip_semantic_equal(doc, saved)


def test_save_outside_primitives_rejected(tmp_path: Path) -> None:
    prim = tmp_path / "primitives"
    prim.mkdir()
    with pytest.raises(twl.TemplateWebError):
        twl.resolve_primitive_save_path("../outside.json", primitives=prim)
    with pytest.raises(twl.TemplateWebError):
        twl.resolve_primitive_save_path("/etc/passwd", primitives=prim)


def _u4_c70_payload() -> dict:
    return {
        "board": FX,
        "chip": "U4",
        "function": "JW5065 bootstrap draft test",
        "id": "draft-test-u4-c70",
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


def test_draft_fx_u4_c70_validates_schema() -> None:
    result = twl.generate_draft(_u4_c70_payload())
    twl.validate_primitive_schema(result["draft"])


def test_draft_is_board_agnostic_and_uses_physical_pins() -> None:
    draft = twl.generate_draft(_u4_c70_payload())["draft"]
    assert draft["strict_pin_matching"] is True
    for sig in draft["signals"]:
        assert "NetC70" not in sig["name_pattern"], "draft leaked board net name"
    patterns = {s["name_pattern"] for s in draft["signals"]}
    assert patterns <= {".*", twl._GND_NET_PATTERN}
    assert twl._GND_NET_PATTERN in patterns
    u4 = next(c for c in draft["components"] if c["id"] == "dcdc")
    assert all(str(p["number"]).isdigit() for p in u4["pins"])
    assert {"1", "2", "3", "4", "5", "6", "8"} <= {str(p["number"]) for p in u4["pins"]}
    by_id = {c["id"]: c for c in draft["components"]}
    assert "pin_names" not in by_id["dcdc"]


def test_replay_uses_submitted_document_not_disk_template() -> None:
    doc = twl.generate_draft(_u4_c70_payload())["draft"]
    first = twl.match_primitive_on_board(doc, FX)
    assert {"U4", "C70"} <= set(first["matched_components"])

    without = json.loads(json.dumps(doc))
    without["components"] = [c for c in without["components"] if c["id"] != "C_bst"]
    second = twl.match_primitive_on_board(without, FX)
    assert "C70" not in second["matched_components"]


def test_save_rejects_unchanged_version_on_behavior_change(tmp_path: Path) -> None:
    prim = tmp_path / "primitives"
    prim.mkdir()
    (prim / "schema.json").write_text(
        (_REPO / "primitives" / "schema.json").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    doc = {
        "id": "t-min",
        "name": "T",
        "version": "0.1.0",
        "signals": [{"id": "a", "type": "net", "name_pattern": ".*"}],
        "components": [
            {"id": "C", "type": "Capacitor", "pins": [{"number": "1", "net": "a"}]}
        ],
    }
    twl.save_primitive("power/t-min.json", doc, enable_write=True, primitives=prim)
    changed = json.loads(json.dumps(doc))
    changed["name"] = "T2"
    with pytest.raises(twl.TemplateWebError) as exc:
        twl.save_primitive("power/t-min.json", changed, enable_write=True, primitives=prim)
    assert exc.value.code == "version_unchanged"
    changed["version"] = "0.1.1"
    assert twl.save_primitive(
        "power/t-min.json", changed, enable_write=True, primitives=prim
    )["schema_ok"]


def test_save_path_suffix_rejected(tmp_path: Path) -> None:
    prim = tmp_path / "primitives"
    prim.mkdir()
    with pytest.raises(twl.TemplateWebError) as exc:
        twl.resolve_primitive_save_path("power/t.txt", primitives=prim)
    assert exc.value.code == "invalid_suffix"



def test_match_fx_includes_u4_and_c70() -> None:
    data = twl.match_board(FX, template_id="jw5065-buck")
    members: set[str] = set()
    for block in data["blocks"]:
        members.update(block["components"])
    assert "U4" in members
    assert "C70" in members


# --------------------------------------------------------------------------
# HTTP level: POST /api/draft without slots takes the automatic path
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def draft_server() -> str:
    import threading
    import urllib.request
    from http.server import HTTPServer

    import template_server

    from circuit_graph.knowledge.db import ComponentDatabase

    template_server.TemplateAPIHandler.db = ComponentDatabase.default()
    httpd = HTTPServer(("127.0.0.1", 0), template_server.TemplateAPIHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def test_post_draft_without_slots_returns_state(draft_server: str) -> None:
    import urllib.request

    body = json.dumps({"board": FX, "chip": "U4"}).encode("utf-8")
    req = urllib.request.Request(
        f"{draft_server}/api/draft",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        assert resp.status == 200
        data = json.loads(resp.read().decode("utf-8"))
    assert data["state"] in {"ready", "needs_review"}
    assert "conflicts" in data
    assert data["pin_source"] == "kb"
    assert "C70" in data["replay"]["matched_components"]
