"""datasheet 应用电路页 ↔ JSON 拓扑对照 — locate / graph / search / render."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from circuit_graph import config  # noqa: E402

config.load()

from datasheet_compare_lib import (  # noqa: E402
    compare_graph,
    locate_datasheet,
    render_page,
    search_page,
    validate_chip,
    validate_page,
    validate_search_text,
)
from template_web_lib import TemplateWebError  # noqa: E402

BOARD = "FX-T268-V6-10米-5065.NET"
CHIP = "U4"


@pytest.fixture(scope="module")
def pdf_env():
    """Datasheet dir + external renderer venv must both be configured."""
    if config.datasheet_dir() is None:
        pytest.skip("DATASHEET_DIR not configured (.env missing?)")
    if config.camelot_venv() is None:
        pytest.skip("CAMELOT_VENV not configured (.env missing?)")
    return True


@pytest.fixture(scope="module")
def locate(pdf_env):
    return locate_datasheet(BOARD, CHIP)


def test_locate_finds_datasheet(locate):
    assert locate["found"] is True
    assert "JW5065" in locate["mpn"]
    assert locate["pdf_name"].lower().endswith(".pdf")
    assert locate["pages"] == 15
    assert locate["default_page"] == 1
    assert locate["renderer"] == "ok"


def test_locate_does_not_expose_absolute_path(locate):
    assert "/" not in locate["pdf_name"]
    assert "\\" not in locate["pdf_name"]


def test_compare_graph_has_bst_with_c70():
    graph = compare_graph(BOARD, CHIP)
    assert graph["chip"]["id"] == "U4"
    assert "JW5065" in graph["chip"]["part_number"]
    pins = {p["name"]: p for p in graph["pins"]}
    assert "BST" in pins
    bst = pins["BST"]
    assert bst["net"]
    member_ids = [m["id"] for m in bst["members"]]
    assert "C70" in member_ids
    # members are capped at 8, overflow reported separately
    assert len(bst["members"]) <= 8


def test_compare_graph_members_are_capped():
    graph = compare_graph(BOARD, CHIP)
    for pin in graph["pins"]:
        assert len(pin["members"]) <= 8
        assert pin["truncated"] >= 0


def test_search_page_bst(locate):
    res = search_page(BOARD, CHIP, locate["default_page"], "BST")
    assert res["rects"]
    for rect in res["rects"]:
        for key in ("x", "y", "w", "h"):
            assert 0.0 <= rect[key] <= 1.0
    assert len(res["page_size"]) == 2


def test_render_page_png(locate):
    try:
        png = render_page(BOARD, CHIP, locate["default_page"], 2)
    except TemplateWebError as exc:
        if exc.status == 503:
            pytest.skip(f"renderer unavailable: {exc}")
        raise
    assert png.startswith(b"\x89PNG")


def test_invalid_chip_rejected():
    with pytest.raises(TemplateWebError):
        validate_chip("..")
    with pytest.raises(TemplateWebError):
        validate_chip("U/4")


def test_invalid_page_rejected():
    with pytest.raises(TemplateWebError):
        validate_page(0)
    with pytest.raises(TemplateWebError):
        validate_page("abc")


def test_invalid_search_text_rejected():
    with pytest.raises(TemplateWebError):
        validate_search_text("")
    with pytest.raises(TemplateWebError):
        validate_search_text("x" * 33)
