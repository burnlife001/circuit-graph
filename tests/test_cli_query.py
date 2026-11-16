"""cg query CLI tests (todolist P1-4)."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import main

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "protel"
FIXTURE = FIXTURE_DIR / "FX-T268-V6-10米-5065.NET"


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_query_conn_text_compact(runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", str(FIXTURE), "--conn", "C9"])
    assert result.exit_code == 0, result.output
    out = result.stdout
    lines = [ln for ln in out.splitlines() if ln.strip()]
    assert len(lines) < 20, f"expected compact output, got {len(lines)} lines"
    assert "NetC9_2" in out
    assert "R26" in out  # co-member with value


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_query_conn_json_shape(runner: CliRunner) -> None:
    result = runner.invoke(
        main, ["query", str(FIXTURE), "--conn", "C9", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    section = data["sections"][0]
    assert section["query"] == "conn"
    assert section["component"]["id"] == "C9"
    pin2 = next(p for p in section["pins"] if p["pin"] == "2")
    assert pin2["net"] == "NetC9_2"
    assert any(m["id"] == "R26" for m in pin2["members"])


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_query_find_value_fuzzy(runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", str(FIXTURE), "--find", "4.7nf"])
    assert result.exit_code == 0, result.output
    assert "C9" in result.stdout
    assert "C33" in result.stdout  # all four snubber caps


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_query_net(runner: CliRunner) -> None:
    result = runner.invoke(
        main, ["query", str(FIXTURE), "--net", "NetC9_2", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    section = json.loads(result.stdout)["sections"][0]
    ids = {m["id"] for m in section["members"]}
    assert ids == {"C9", "R26"}


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_query_comp_and_type_filter(runner: CliRunner) -> None:
    result = runner.invoke(
        main, ["query", str(FIXTURE), "--comp", "R26", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    section = json.loads(result.stdout)["sections"][0]
    assert section["id"] == "R26"
    assert section["nets"]["1"] == "SW2"

    result = runner.invoke(
        main, ["query", str(FIXTURE), "--all", "--type", "resistor", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)["sections"][0]["components"]
    assert rows and all(r["type"] == "resistor" for r in rows)


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_query_missing_selector_errors(runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", str(FIXTURE)])
    assert result.exit_code != 0


@pytest.mark.skipif(not FIXTURE.exists(), reason=f"Fixture not found: {FIXTURE}")
def test_query_unknown_component(runner: CliRunner) -> None:
    result = runner.invoke(main, ["query", str(FIXTURE), "--conn", "ZZZ9"])
    assert result.exit_code == 1
    assert "not found" in result.stderr
