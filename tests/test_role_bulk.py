"""批量定性命令测试：`cg role --all` 与 `cg primitives coverage`。

只依赖本地 fixture 与本地 primitives 模板库；KB 缺失时命令降级不崩，
因此断言不涉及 KB 具体内容，也不访问网络。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import main

FIXTURE = (
    Path(__file__).resolve().parent.parent / "fixtures" / "protel" / "FX-T268-V6-10米-5065.NET"
)

pytestmark = pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


def _role_all(runner: CliRunner) -> dict:
    result = runner.invoke(
        main, ["role", "--all", str(FIXTURE), "--format", "json", "--no-cache"]
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _component_count() -> int:
    from circuit_graph.cli.app import _parse_and_enrich

    return len(_parse_and_enrich(FIXTURE, use_cache=False).components)


# -- cg role --all ---------------------------------------------------------


def test_role_all_json_shape(runner: CliRunner) -> None:
    data = _role_all(runner)
    assert data["count"] == len(data["results"])
    assert data["count"] >= 150
    for item in data["results"]:
        for key in ("designator", "rule", "title", "summary", "confidence"):
            assert key in item, item


def test_role_all_covers_every_component(runner: CliRunner) -> None:
    data = _role_all(runner)
    assert data["count"] == _component_count()


def test_role_all_r100_rule(runner: CliRunner) -> None:
    data = _role_all(runner)
    r100 = next(r for r in data["results"] if r["designator"] == "R100")
    assert "opamp-noninv-feedback" in r100["rule"]


def test_role_single_matches_bulk_result(runner: CliRunner) -> None:
    """单元件模式与 --all 里同一条结果必须一致（回归护栏）。"""
    single = runner.invoke(
        main, ["role", "R100", str(FIXTURE), "--format", "json", "--no-cache"]
    )
    assert single.exit_code == 0, single.output
    one = json.loads(single.stdout)

    data = _role_all(runner)
    bulk = next(r for r in data["results"] if r["designator"] == "R100")

    for key in ("designator", "rule", "title", "summary", "confidence"):
        assert one[key] == bulk[key], key


def test_role_all_rejects_designator(runner: CliRunner) -> None:
    result = runner.invoke(main, ["role", "--all", "R100", str(FIXTURE)])
    assert result.exit_code == 2


# -- cg primitives coverage ------------------------------------------------


def test_primitives_coverage_json(runner: CliRunner) -> None:
    result = runner.invoke(
        main, ["primitives", "coverage", str(FIXTURE), "--format", "json", "--no-cache"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)

    totals = data["totals"]
    assert totals["components"] == totals["matched"] + totals["unmatched"]
    assert totals["components"] == _component_count()
    assert data["unmatched"], "fixture 里应当有未命中任何模板的元件"

    detector = next(t for t in data["templates"] if t["id"] == "detector-amp-sampling")
    assert len(detector["components"]) >= 9

    members = [c for t in data["templates"] for c in t["components"]]
    assert len(members) == len(set(members)), "元件不得跨模板重复"
    assert set(members) | set(data["unmatched"]) == {
        c.id for c in _parse_components()
    }
    assert not (set(members) & set(data["unmatched"]))


def test_primitives_coverage_text(runner: CliRunner) -> None:
    result = runner.invoke(main, ["primitives", "coverage", str(FIXTURE), "--no-cache"])
    assert result.exit_code == 0, result.output
    first = result.stdout.splitlines()[0]
    assert first.startswith("总计 ")
    assert "未命中" in first


def _parse_components():
    from circuit_graph.cli.app import _parse_and_enrich

    return _parse_and_enrich(FIXTURE, use_cache=False).components


def test_bulk_matches_single_for_live_matched_components() -> None:
    """共享 session 的现场匹配结果必须与"每次独立 session"逐字段一致。

    批量路径共享现场模板搜索的缓存，这段代码没有别的护栏，必须钉死。
    """
    from circuit_graph.cli.app import DEFAULT_KB_PATH, _parse_and_enrich
    from circuit_graph.cli.role import _kb_pinout_provider
    from circuit_graph.role import LiveMatchSession, infer_role

    ir = _parse_and_enrich(FIXTURE, use_cache=False)
    provider = _kb_pinout_provider(DEFAULT_KB_PATH)
    session = LiveMatchSession(ir)

    live_ids: list[str] = []
    for comp in ir.components:
        result = infer_role(ir, comp.id, provider, live_session=session)
        if result is not None and result.rule.startswith("primitive-live:"):
            live_ids.append(comp.id)
        if len(live_ids) >= 3:
            break

    assert live_ids, "fixture 里应当有走现场模板匹配的元件"
    for cid in live_ids:
        bulk = infer_role(ir, cid, provider, live_session=session)
        single = infer_role(ir, cid, provider)  # 无共享 session = 改造前的行为
        assert bulk is not None and single is not None
        assert (
            bulk.rule,
            bulk.title,
            bulk.summary,
            bulk.confidence,
        ) == (single.rule, single.title, single.summary, single.confidence), cid


def test_template_of_block_longest_prefix() -> None:
    """模板 id 解出必须取最长前缀，且前缀后必须是分隔符或结尾。"""
    from circuit_graph.cli.primitives import _template_of_block

    ids = ["rc-snubber", "rc-snubber-series", "rc-snubber-shunt", "opamp-unit"]
    assert _template_of_block("rc-snubber-series-r1", ids) == "rc-snubber-series"
    assert _template_of_block("rc-snubber-shunt-c3", ids) == "rc-snubber-shunt"
    assert _template_of_block("rc-snubber", ids) == "rc-snubber"
    # "rc-snubberXYZ" 不是 "rc-snubber-" 实例，不该误配
    assert _template_of_block("rc-snubberXYZ", ids) is None
    assert _template_of_block("unknown-thing-r1", ids) is None


def test_natural_key_orders_designators() -> None:
    from circuit_graph.cli.primitives import _natural_key

    got = sorted(["R10", "R2", "R1", "C9", "C10"], key=_natural_key)
    assert got == ["C9", "C10", "R1", "R2", "R10"]
