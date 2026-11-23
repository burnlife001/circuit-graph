"""`chip_resolver.jlc_search_results` 对裸 C 号的精确命中约束。

JLCPCB 模糊搜索会把 ``CLC5665IMX``（含相同数字）排在 ``C5665`` 真器件前面，
所以裸 C 号查询必须按 ``lcsc_id`` 严格过滤，查不到就报错，不静默降级。
全部用 fake 子进程输出，不联网。
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from circuit_graph.knowledge import chip_resolver

PAYLOAD: dict[str, Any] = {
    "success": True,
    "results": [
        {"lcsc_id": "C123", "name": "CLC5665IMX", "package": "SOP-8"},
        {"lcsc_id": "C5665", "name": "REAL-PART", "package": "SOT-23-5"},
        {"lcsc_id": "C9999", "name": "OTHER", "package": "QFN-16"},
    ],
}


def _fake_jlc(monkeypatch, payload: dict[str, Any] | None = None) -> list[list[str]]:
    """把 ``subprocess.run`` 换成回放固定 JSON，并记录调用参数。"""
    calls: list[list[str]] = []

    def fake_run(cmd, **kwargs):  # noqa: ANN001, ANN003
        calls.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, stdout=json.dumps(payload if payload is not None else PAYLOAD), stderr=""
        )

    monkeypatch.setattr(chip_resolver.subprocess, "run", fake_run)
    return calls


def test_bare_lcsc_filters_to_exact_result(monkeypatch) -> None:
    _fake_jlc(monkeypatch)

    results = chip_resolver.jlc_search_results("C5665", limit=3)

    assert [r["lcsc_id"] for r in results] == ["C5665"]
    assert results[0]["name"] == "REAL-PART"


def test_bare_lcsc_lowercase_query_still_matches(monkeypatch) -> None:
    _fake_jlc(monkeypatch)

    results = chip_resolver.jlc_search_results("c5665", limit=3)

    assert [r["lcsc_id"] for r in results] == ["C5665"]


def test_bare_lcsc_without_exact_hit_raises(monkeypatch) -> None:
    _fake_jlc(monkeypatch)

    with pytest.raises(RuntimeError, match="No exact LCSC match"):
        chip_resolver.jlc_search_results("C7777", limit=3)


def test_bare_lcsc_missing_lcsc_id_key_raises(monkeypatch) -> None:
    _fake_jlc(monkeypatch, {"success": True, "results": [{"name": "NO-ID"}]})

    with pytest.raises(RuntimeError, match="No exact LCSC match"):
        chip_resolver.jlc_search_results("C5665")


def test_part_number_query_keeps_fuzzy_ranking(monkeypatch) -> None:
    _fake_jlc(monkeypatch)

    results = chip_resolver.jlc_search_results("CLC5665", limit=3)

    assert [r["lcsc_id"] for r in results] == ["C123", "C5665", "C9999"]


def test_jlc_search_propagates_exact_miss(monkeypatch) -> None:
    _fake_jlc(monkeypatch)

    with pytest.raises(RuntimeError, match="No exact LCSC match"):
        chip_resolver.jlc_search("C7777")


def test_exact_hit_survives_package_widened_fetch(monkeypatch) -> None:
    calls = _fake_jlc(monkeypatch)

    lcsc_id, package = chip_resolver.jlc_search("C5665", package="SOT-23-5")

    assert (lcsc_id, package) == ("C5665", "SOT-23-5")
    # 带封装会放大抓取窗口，但仍只保留精确命中的那一条。
    assert calls[0][-1] == "--json"
    assert "10" in calls[0]
    assert len([r for r in PAYLOAD["results"] if r["lcsc_id"] == "C5665"]) == 1
