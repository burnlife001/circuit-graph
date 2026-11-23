"""``cg role --kb-source`` 的 CLI 行为测试。

全部离线：网络 / 外部 CLI / PDF 提取都用 mock 顶替，
断言的是**行为合同**（默认离线、三种来源、作用范围、失败不伪造引脚）。
"""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

import pytest
from click.testing import CliRunner

from circuit_graph.cli import main
from circuit_graph.knowledge.db import ComponentDatabase

# 迷你板：U1 运放 + R1 反馈 + R2 增益腿 + U2（第二颗 IC，用于 --all 范围测试）
MINI_NETLIST = """[
R1
0805
10K
]
[
R2
0805
10K
]
[
U1
SOT23-5
OPAMP1
]
[
U2
SOT23-5
OPAMP2
]
(
VOUT
U1-3
R1-1
)
(
NEG
U1-4
R1-2
R2-1
)
(
GND
U1-2
R2-2
)
(
VOUT2
U2-3
)
(
GND2
U2-2
)
"""

OPAMP_PINS = {"1": "+", "2": "GND", "3": "OUT", "4": "-", "5": "V+"}


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def netlist(tmp_path: Path) -> Path:
    p = tmp_path / "mini.NET"
    p.write_text(MINI_NETLIST, encoding="utf-8")
    return p


@pytest.fixture
def kb_path(tmp_path: Path) -> Path:
    return tmp_path / "kb.sqlite"


def _pinout_of(kb_path: Path, part_number: str) -> dict[str, str] | None:
    db = ComponentDatabase.open(kb_path)
    try:
        return db.get_pinout(part_number)
    finally:
        db.close()


def _forbid(*_args, **_kwargs):
    raise AssertionError("默认路径不允许联网 / 调外部 CLI")


@pytest.fixture
def offline_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    """把所有可能的外部出口都变成硬失败。"""
    import circuit_graph.knowledge.chip_resolver as chip_resolver

    monkeypatch.setattr(chip_resolver, "resolve_part", _forbid)
    monkeypatch.setattr(chip_resolver, "jlc_search", _forbid)
    monkeypatch.setattr(chip_resolver, "resolve_lcsc", _forbid)
    monkeypatch.setattr(urllib.request, "urlopen", _forbid)
    monkeypatch.setattr(subprocess, "run", _forbid)


# ---------------------------------------------------------------------------
# 默认离线
# ---------------------------------------------------------------------------


def test_default_run_is_strictly_offline(runner, netlist, kb_path, offline_guard) -> None:
    """不传 --kb-source：不得联网、不得调外部解析 CLI。"""
    result = runner.invoke(
        main, ["role", "R1", str(netlist), "--db-path", str(kb_path), "--no-cache"]
    )
    assert result.exit_code == 0, result.output
    assert "R1" in result.stdout


def test_default_run_does_not_write_kb(runner, netlist, kb_path, offline_guard) -> None:
    """默认路径只读 KB：不写任何引脚数据。"""
    runner.invoke(
        main, ["role", "R1", str(netlist), "--db-path", str(kb_path), "--no-cache"]
    )
    assert _pinout_of(kb_path, "OPAMP1") is None


# ---------------------------------------------------------------------------
# 三种来源
# ---------------------------------------------------------------------------


def test_pin2json_source_fills_kb_and_is_used_by_role(
    runner, netlist, kb_path, monkeypatch
) -> None:
    """pin2json：KB 缺失 → 确定性解析 → 入库 → role 用上新引脚。

    单位号查询只补目标（U1）；被动件 R1 的定性受益走离线复查——
    R1 自身不再触发相邻 IC 补全（那是被禁止的隐式批量联网）。
    """
    import circuit_graph.knowledge.chip_resolver as chip_resolver

    calls: list[str] = []

    def fake_resolve(part_number: str, package=None):
        calls.append(part_number)
        return {
            "symbol": part_number,
            "package": package,
            "source": "C0000",
            "pin_count": len(OPAMP_PINS),
            "pins": dict(OPAMP_PINS),
        }

    monkeypatch.setattr(chip_resolver, "resolve_part", fake_resolve)

    result = runner.invoke(
        main,
        [
            "role", "U1", str(netlist),
            "--kb-source", "pin2json",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    # 只补目标元件，不碰板上的其它型号
    assert calls == ["OPAMP1"]
    # KB 里确实写入了引脚
    assert _pinout_of(kb_path, "OPAMP1") == OPAMP_PINS
    # 补进 KB 的引脚随后就能被 role 使用（离线复查）：R1 变成运放反馈电阻
    followup = runner.invoke(
        main,
        ["role", "R1", str(netlist), "--db-path", str(kb_path), "--no-cache"],
    )
    assert followup.exit_code == 0, followup.output
    assert "反馈电阻" in followup.stdout


def test_datasheet_source_fills_kb(runner, netlist, kb_path, monkeypatch) -> None:
    """datasheet：走本地 PDF 查找 + pdf_pin_extract 提取链路。"""
    import circuit_graph.knowledge.pdf_pin_extract as pdf_pin_extract

    monkeypatch.setattr(
        pdf_pin_extract, "extract_from_datasheet", lambda part: dict(OPAMP_PINS)
    )

    result = runner.invoke(
        main,
        [
            "role", "U1", str(netlist),
            "--kb-source", "datasheet",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _pinout_of(kb_path, "OPAMP1") == OPAMP_PINS
    # 补进 KB 的引脚随后就能被 role 使用（离线复查）：R1 变成运放反馈电阻
    followup = runner.invoke(
        main,
        ["role", "R1", str(netlist), "--db-path", str(kb_path), "--no-cache"],
    )
    assert followup.exit_code == 0, followup.output
    assert "反馈电阻" in followup.stdout


def test_url_source_downloads_and_extracts(runner, netlist, kb_path, tmp_path, monkeypatch) -> None:
    """URL 来源：下载 PDF 到缓存 → 复用同一条确定性 PDF 提取链路。"""
    import circuit_graph.knowledge.pdf_pin_extract as pdf_pin_extract

    downloaded: list[str] = []

    class _FakeResponse:
        def read(self) -> bytes:
            return b"%PDF-1.4 fake datasheet"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url, timeout=None):
        downloaded.append(url)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(pdf_pin_extract, "search_pin_pages", lambda pdf: [1])
    monkeypatch.setattr(pdf_pin_extract, "extract_pin_table", lambda pdf, pages: dict(OPAMP_PINS))

    cache_dir = tmp_path / "cache"
    result = runner.invoke(
        main,
        [
            "role", "U1", str(netlist),
            "--kb-source", "https://example.com/OPAMP1.pdf",
            "--db-path", str(kb_path),
            "--cache-dir", str(cache_dir),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert downloaded == ["https://example.com/OPAMP1.pdf"]
    assert _pinout_of(kb_path, "OPAMP1") == OPAMP_PINS
    # PDF 落到缓存里
    pdfs = list((cache_dir / "datasheets").glob("*.pdf"))
    assert pdfs and pdfs[0].read_bytes().startswith(b"%PDF")


def test_url_source_rejects_non_pdf(runner, netlist, kb_path, monkeypatch) -> None:
    """URL 指向的不是 PDF：清楚报告失败，不猜引脚。"""

    class _FakeResponse:
        def read(self) -> bytes:
            return b"<html>not a pdf</html>"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: _FakeResponse())

    result = runner.invoke(
        main,
        [
            "role", "U1", str(netlist),
            "--kb-source", "https://example.com/not-a-ds.pdf",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "PDF" in result.stderr
    assert _pinout_of(kb_path, "OPAMP1") is None


# ---------------------------------------------------------------------------
# 作用范围
# ---------------------------------------------------------------------------


def test_single_designator_only_fills_that_target(runner, netlist, kb_path, monkeypatch) -> None:
    import circuit_graph.knowledge.chip_resolver as chip_resolver

    calls: list[str] = []

    def fake_resolve(part_number: str, package=None):
        calls.append(part_number)
        return {"pins": dict(OPAMP_PINS), "package": package, "pin_count": 5}

    monkeypatch.setattr(chip_resolver, "resolve_part", fake_resolve)

    result = runner.invoke(
        main,
        [
            "role", "U1", str(netlist),
            "--kb-source", "pin2json",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert calls == ["OPAMP1"]
    assert _pinout_of(kb_path, "OPAMP2") is None


def test_all_fills_every_non_passive_part(runner, netlist, kb_path, monkeypatch) -> None:
    import circuit_graph.knowledge.chip_resolver as chip_resolver

    calls: list[str] = []

    def fake_resolve(part_number: str, package=None):
        calls.append(part_number)
        return {"pins": dict(OPAMP_PINS), "package": package, "pin_count": 5}

    monkeypatch.setattr(chip_resolver, "resolve_part", fake_resolve)

    result = runner.invoke(
        main,
        [
            "role", "--all", str(netlist),
            "--kb-source", "pin2json",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    # 两只 IC 都补；电阻（R1/R2）属被动器件，不查
    assert sorted(calls) == ["OPAMP1", "OPAMP2"]


def test_url_with_all_is_rejected(runner, netlist, kb_path) -> None:
    """URL + --all 组合必须拒绝并说明原因。"""
    result = runner.invoke(
        main,
        [
            "role", "--all", str(netlist),
            "--kb-source", "https://example.com/ds.pdf",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 2
    assert "URL" in result.stderr or "url" in result.stderr


def test_unknown_source_is_rejected(runner, netlist, kb_path) -> None:
    result = runner.invoke(
        main,
        [
            "role", "R1", str(netlist),
            "--kb-source", "bogus",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 2
    assert "bogus" in result.stderr


# ---------------------------------------------------------------------------
# 失败不阻断、不伪造
# ---------------------------------------------------------------------------


def test_failure_does_not_block_other_parts_and_writes_no_fake_pins(
    runner, netlist, kb_path, monkeypatch
) -> None:
    """一个元件解析失败：其余照常补；失败的元件不产生任何引脚数据。"""
    import circuit_graph.knowledge.chip_resolver as chip_resolver

    def fake_resolve(part_number: str, package=None):
        if part_number == "OPAMP2":
            raise RuntimeError("jlc search failed")
        return {"pins": dict(OPAMP_PINS), "package": package, "pin_count": 5}

    monkeypatch.setattr(chip_resolver, "resolve_part", fake_resolve)

    result = runner.invoke(
        main,
        [
            "role", "--all", str(netlist),
            "--kb-source", "pin2json",
            "--db-path", str(kb_path),
            "--no-cache",
            "--format", "json",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    fill = payload["kb_fill"]
    assert [r["part_number"] for r in fill["resolved"]] == ["OPAMP1"]
    assert [r["part_number"] for r in fill["failed"]] == ["OPAMP2"]
    assert _pinout_of(kb_path, "OPAMP1") == OPAMP_PINS
    assert _pinout_of(kb_path, "OPAMP2") is None


def test_package_mismatch_is_not_written(runner, netlist, kb_path, monkeypatch) -> None:
    """封装对不上：按失败报告，不把别的变体的引脚名安到板上。"""
    import circuit_graph.knowledge.chip_resolver as chip_resolver

    def fake_resolve(part_number: str, package=None):
        return {
            "pins": dict(OPAMP_PINS),
            "package": "QFN-32",
            "pin_count": 5,
        }

    monkeypatch.setattr(chip_resolver, "resolve_part", fake_resolve)

    result = runner.invoke(
        main,
        [
            "role", "U1", str(netlist),
            "--kb-source", "pin2json",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "封装" in result.stderr
    assert _pinout_of(kb_path, "OPAMP1") is None


# ---------------------------------------------------------------------------
# 入库校验（错误来源数据不得写进 KB）
# ---------------------------------------------------------------------------


# U9 接出 10 个引脚的板：核验"来源引脚数/编号对不上板"需要板上引脚多于来源
WIDE_NETLIST = """[
U9
TQFP32
MCU1
]
(
N1
U9-1
R1-1
)
(
N2
U9-2
)
(
N3
U9-3
)
(
N4
U9-4
)
(
N5
U9-5
)
(
N6
U9-6
)
(
N7
U9-7
)
(
N8
U9-8
)
(
N9
U9-9
)
(
N10
U9-10
)
[
R1
0805
10K
]
"""


@pytest.fixture
def wide_netlist(tmp_path: Path) -> Path:
    p = tmp_path / "wide.NET"
    p.write_text(WIDE_NETLIST, encoding="utf-8")
    return p


def _resolve_n_pins(n: int, package=None):
    def _resolve(part_number: str, package=None):
        return {
            "pins": {str(i): f"P{i}" for i in range(1, n + 1)},
            "package": package,
            "pin_count": n,
        }

    return _resolve


def test_url_source_rejects_wrong_part(
    runner, netlist, kb_path, tmp_path, monkeypatch
) -> None:
    """URL datasheet 的型号核验不了（文件名与 PDF 内容都没有目标型号）：拒绝入库。"""
    import circuit_graph.knowledge.kb_source as kb_source

    class _FakeResponse:
        def read(self) -> bytes:
            return b"%PDF-1.4 fake datasheet"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: _FakeResponse())
    monkeypatch.setattr(kb_source, "_pdf_contains", lambda pdf, part: False)

    result = runner.invoke(
        main,
        [
            "role", "U1", str(netlist),
            "--kb-source", "https://example.com/AT45DB321D-datasheet.pdf",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "AT45DB321D" in result.stderr or "核验" in result.stderr
    assert _pinout_of(kb_path, "OPAMP1") is None


def test_url_source_accepts_when_pdf_content_proves_part(
    runner, netlist, kb_path, tmp_path, monkeypatch
) -> None:
    """URL 文件名没有型号、但 PDF 内容里有：仍可入库。"""
    import circuit_graph.knowledge.kb_source as kb_source
    import circuit_graph.knowledge.pdf_pin_extract as pdf_pin_extract

    class _FakeResponse:
        def read(self) -> bytes:
            return b"%PDF-1.4 fake datasheet"

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda url, timeout=None: _FakeResponse())
    monkeypatch.setattr(kb_source, "_pdf_contains", lambda pdf, part: True)
    monkeypatch.setattr(pdf_pin_extract, "search_pin_pages", lambda pdf: [1])
    monkeypatch.setattr(
        pdf_pin_extract, "extract_pin_table", lambda pdf, pages: dict(OPAMP_PINS)
    )

    result = runner.invoke(
        main,
        [
            "role", "U1", str(netlist),
            "--kb-source", "https://example.com/unknown-name-datasheet.pdf",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert _pinout_of(kb_path, "OPAMP1") == OPAMP_PINS


def test_wrong_pin_count_is_not_written_even_with_matching_package(
    runner, wide_netlist, kb_path, monkeypatch
) -> None:
    """来源引脚对不上板上接出的引脚：即使封装名一致也拒绝。"""
    import circuit_graph.knowledge.chip_resolver as chip_resolver

    monkeypatch.setattr(chip_resolver, "resolve_part", _resolve_n_pins(8, "TQFP32"))

    result = runner.invoke(
        main,
        [
            "role", "U9", str(wide_netlist),
            "--kb-source", "pin2json",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "不写入" in result.stderr
    assert _pinout_of(kb_path, "MCU1") is None


def test_wrong_pin_count_rejected_without_package(
    runner, wide_netlist, kb_path, monkeypatch
) -> None:
    """来源没有 package 信息时也要按引脚核验——这正是"8 脚 PDF 写给
    32 脚器件"漏网的路径。"""
    import circuit_graph.knowledge.chip_resolver as chip_resolver

    monkeypatch.setattr(chip_resolver, "resolve_part", _resolve_n_pins(8, None))

    result = runner.invoke(
        main,
        [
            "role", "U9", str(wide_netlist),
            "--kb-source", "pin2json",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "不写入" in result.stderr
    assert _pinout_of(kb_path, "MCU1") is None


# ---------------------------------------------------------------------------
# 单位号只补目标；无补全目标不崩
# ---------------------------------------------------------------------------


def test_single_passive_designator_fills_nothing_and_stays_offline(
    runner, netlist, kb_path, offline_guard
) -> None:
    """单位号是被动件：只补目标 = 什么都不补，也不得联网去抓相邻 IC。

    offline_guard 把 chip_resolver / urlopen / subprocess 全部变成硬失败：
    只要代码试图为 R1 的相邻 IC 联网，本测试就会炸。
    """
    result = runner.invoke(
        main,
        [
            "role", "R1", str(netlist),
            "--kb-source", "pin2json",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "没有可补全的目标" in result.stderr
    assert _pinout_of(kb_path, "OPAMP1") is None
    assert _pinout_of(kb_path, "OPAMP2") is None


def test_unknown_designator_with_kb_source_reports_not_found(
    runner, netlist, kb_path
) -> None:
    """板上不存在的位号 + --kb-source：不得崩，保留"元件不存在"退出语义。"""
    result = runner.invoke(
        main,
        [
            "role", "NOPE", str(netlist),
            "--kb-source", "pin2json",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 1, result.output
    assert "component not found" in result.stderr


PASSIVE_NETLIST = """[
R1
0805
10K
]
[
R2
0805
10K
]
(
N1
R1-1
R2-1
)
(
N2
R1-2
R2-2
)
"""


def test_all_on_passive_only_board_does_not_crash(runner, kb_path, tmp_path) -> None:
    """全是被动件的板 + --all --kb-source：空 FillReport + 明确提示，不崩。"""
    p = tmp_path / "passive.NET"
    p.write_text(PASSIVE_NETLIST, encoding="utf-8")
    result = runner.invoke(
        main,
        [
            "role", "--all", str(p),
            "--kb-source", "pin2json",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "没有可补全的目标" in result.stderr
    assert _pinout_of(kb_path, "OPAMP1") is None


def test_isolated_passive_designator_does_not_crash(runner, kb_path, tmp_path) -> None:
    """孤立被动件（邻居里没有 IC）+ --kb-source：不崩、明确提示。"""
    p = tmp_path / "passive.NET"
    p.write_text(PASSIVE_NETLIST, encoding="utf-8")
    result = runner.invoke(
        main,
        [
            "role", "R1", str(p),
            "--kb-source", "pin2json",
            "--db-path", str(kb_path),
            "--no-cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "没有可补全的目标" in result.stderr
