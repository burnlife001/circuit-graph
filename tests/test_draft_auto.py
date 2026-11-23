"""Tests for the automatic (board + chip only) template draft."""

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


@pytest.fixture(scope="module")
def auto() -> dict:
    """One auto draft for the whole module (the replay is not cheap)."""
    return twl.auto_draft(FX, "U4")


def test_auto_draft_validates_schema_and_uses_kb_pinout(auto: dict) -> None:
    twl.validate_primitive_schema(auto["draft"])
    assert auto["pin_source"] == "kb"
    assert auto["state"] in {"ready", "needs_review"}
    assert auto["board"] == FX
    assert auto["chip"] == "U4"


def test_auto_draft_is_board_agnostic(auto: dict) -> None:
    blob = json.dumps(auto["draft"], ensure_ascii=False)
    assert "C70" not in blob, "draft leaked a board designator"
    assert "NetC70" not in blob, "draft leaked a board net name"
    patterns = {s["name_pattern"] for s in auto["draft"]["signals"]}
    assert patterns <= {".*", twl._GND_NET_PATTERN}
    signal_ids = {s["id"] for s in auto["draft"]["signals"]}
    assert "bst" in signal_ids


def test_auto_draft_replay_matches_c70(auto: dict) -> None:
    matched = set(auto["replay"]["matched_components"])
    assert "C70" in matched
    conflicts = auto["conflicts"]
    assert "no_match" not in conflicts
    assert "unexpected:C70" not in conflicts


def test_auto_draft_survives_pdf_extraction_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from circuit_graph.knowledge import pdf_pin_extract

    def _boom(_part_number: str) -> dict[str, str] | None:
        raise RuntimeError("camelot exploded")

    monkeypatch.setattr(pdf_pin_extract, "extract_from_datasheet", _boom)
    result = twl.auto_draft(FX, "U4")
    twl.validate_primitive_schema(result["draft"])
    assert result["pin_source"] != "pdf"


def test_legacy_generate_draft_still_works_with_slots() -> None:
    payload = {
        "board": FX,
        "chip": "U4",
        "function": "JW5065 bootstrap draft test",
        "id": "draft-auto-legacy",
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
    draft = twl.generate_draft(payload)["draft"]
    member_ids = {c["id"] for c in draft["components"]}
    assert "C_bst" in member_ids
    assert "dcdc" in member_ids
