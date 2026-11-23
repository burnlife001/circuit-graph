"""cg primitives —— primitives 模板工具组。

    cg primitives coverage board.NET               # 每个元件命中了哪个模板
    cg primitives coverage board.NET --format json
    cg primitives draft board.NET --all --write     # 板 + 芯片 → 自动模板（过机器闸门才写）

coverage 的块归属**直接消费 role 引擎的结论**（``RoleResult.data["template_id"]``）：
每个元件归到 role 给它选定的那个模板，一个元件只归一个组。
这样 coverage 与 role 只有一份块归属，不再出现"同一元件两边归到不同模板"
的分裂（旧的按 ir.blocks 顺序先到先得的做法会让 U4 归 buck-converter，
而 role 按 superset 规则归 jw5065-buck）。

注意：本命令统计的是"role 判定的模板块归属"覆盖度，不是"识别器产出的功能块"
覆盖度 —— 既不在任何功能块里、也没有走通现场模板匹配的元件算作未命中。
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from pathlib import Path

import click

from circuit_graph.cli.app import (
    _parse_and_enrich,
    build_pin_category_provider,
    main,
)

#: 完整批量报告的落点（output/ 不入库）。
AUTO_REPORT_PATH = Path("output/auto-draft-report.md")

_NATURAL_RE = re.compile(r"(\d+)")
_MAX_UNMATCHED_SHOWN = 20


def _natural_key(text: str) -> tuple:
    """位号自然序：R2 < R10（字母段按字典序，数字段按数值）。"""
    parts = [p for p in _NATURAL_RE.split(text) if p != ""]
    return tuple((1, int(p), "") if p.isdigit() else (0, 0, p) for p in parts)


def _pad(text: str, width: int) -> str:
    """按显示宽度左对齐（中日韩字符占 2 列，否则 --all 的表格列会错位）。"""
    shown = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)
    return text + " " * max(0, width - shown)


def _template_of_block(block_id: str, template_ids: list[str]) -> str | None:
    """用最长前缀匹配从 block.id 解出模板 id。

    前缀必须落在分隔符（``-``）或字符串结尾上，否则 "rc-low" 会吃掉
    "rc-low-pass-x" 里的 "rc-low" 那一类误配。
    """
    from circuit_graph.primitives import template_of_block

    return template_of_block(block_id, template_ids)


def _role_template_attribution(ir, db_path) -> dict[str, str | None]:
    """元件 → role 引擎判定的模板块归属（**单一来源**）。

    coverage 与 role 曾各算各的块归属：coverage 按 ``ir.blocks`` 的出现
    顺序"先到先得"（模板按 id 字母序处理），role 则按"具体块优先 +
    superset 规则"选块 —— 同一个元件两边可能归到不同模板（U4：coverage 说
    buck-converter，role 说 jw5065-buck），覆盖率报告与定性报告互相打架，
    block-drift 报警变成假报警。

    这里直接消费 role 的结论（``RoleResult.data["template_id"]``）：
    两个引擎从此只有一份块归属，coverage 不再自己猜。
    """
    from circuit_graph.cli.app import DEFAULT_KB_PATH
    from circuit_graph.cli.role import _kb_facts_provider, _kb_pinout_provider
    from circuit_graph.role import LiveMatchSession, infer_role

    kb_path = db_path if db_path is not None else DEFAULT_KB_PATH
    provider = _kb_pinout_provider(kb_path)
    facts = _kb_facts_provider(kb_path)
    session = LiveMatchSession(ir, kb_path=kb_path)

    out: dict[str, str | None] = {}
    for comp in ir.components:
        result = infer_role(
            ir, comp.id, provider, live_session=session, facts_provider=facts
        )
        out[comp.id] = result.data.get("template_id") if result is not None else None
    return out


@main.group(name="primitives")
def primitives() -> None:
    """Primitive template tooling."""


@primitives.command(name="coverage")
@click.argument(
    "file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
)
@click.option(
    "--format",
    "out_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
@click.option(
    "--db-path",
    type=click.Path(path_type=Path),
    default=None,
    help=(
        "KB SQLite path (default: <pkg>/knowledge/circuit_graph.db)。"
        "命中 IR 缓存时沿用缓存里已算好的功能块，--db-path 不参与缓存键。"
    ),
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="IR cache directory (default: <repo>/.cg-cache)。",
)
@click.option("--no-cache", is_flag=True, help="Bypass the IR parse/enrich cache.")
def coverage(
    file: Path,
    out_format: str,
    db_path: Path | None,
    cache_dir: Path | None,
    no_cache: bool,
) -> None:
    """Show which primitive template each component belongs to."""
    from circuit_graph.primitives import load_primitives

    ir = _parse_and_enrich(
        file,
        cache_dir=cache_dir,
        use_cache=not no_cache,
        pin_category_provider=build_pin_category_provider(db_path),
    )

    templates = load_primitives()
    template_ids = [t.id for t in templates]
    names = {t.id: t.name for t in templates}

    known_components = {c.id for c in ir.components}
    groups: dict[str, list[str]] = {}
    assigned: set[str] = set()
    # 块归属来自 role 引擎（见 _role_template_attribution）：coverage 与 role
    # 只有一份块归属，不再各算各的。
    for cid, tid in _role_template_attribution(ir, db_path).items():
        if not tid or cid not in known_components or cid in assigned:
            continue
        assigned.add(cid)
        groups.setdefault(tid, []).append(cid)

    template_rows = [
        {
            "id": tid,
            "name": names.get(tid, tid),
            "components": sorted(members, key=_natural_key),
        }
        for tid, members in sorted(groups.items())
    ]
    unmatched = sorted(known_components - assigned, key=_natural_key)
    totals = {
        "components": len(known_components),
        "matched": len(assigned),
        "unmatched": len(unmatched),
        "templates": len(template_rows),
    }

    if out_format == "json":
        payload = {
            "netlist": file.as_posix(),
            "totals": totals,
            "templates": template_rows,
            "unmatched": unmatched,
        }
        sys.stdout.buffer.write(
            (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
        return

    lines = [
        f"总计 {totals['components']} 元件：命中 {totals['matched']}"
        f"（{totals['templates']} 个模板），未命中 {totals['unmatched']}"
    ]
    for row in template_rows:
        members = ", ".join(row["components"])
        lines.append(f"  {row['id']} ({row['name']})：{len(row['components'])} 个 → {members}")
    shown = unmatched[:_MAX_UNMATCHED_SHOWN]
    tail = " ..." if len(unmatched) > _MAX_UNMATCHED_SHOWN else ""
    lines.append(f"未命中（共 {len(unmatched)} 个）：{', '.join(shown)}{tail}")
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))


def _rebuild_primitive_index(primitives_dir: Path | None = None) -> str:
    """重建 ``primitives/__index_primitives.md``（直接调脚本里的函数，不重复实现）。"""
    import importlib.util

    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "build_primitive_index.py"
    spec = importlib.util.spec_from_file_location("cg_build_primitive_index", script)
    if spec is None or spec.loader is None:  # pragma: no cover - 脚本缺失
        return ""
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    base = Path(primitives_dir) if primitives_dir else module.PRIMITIVES_DIR
    rows = module.collect_primitives(base)
    index_path = base / module.INDEX_PATH.name
    index_path.write_text(module.render_markdown(rows), encoding="utf-8", newline="\n")
    try:
        rel = index_path.relative_to(module.REPO_ROOT).as_posix()
    except ValueError:
        rel = index_path.as_posix()
    return f"{rel} ({len(rows)} entries)"


@primitives.command(name="draft")
@click.argument("board")
@click.option(
    "--chip",
    "chips",
    multiple=True,
    help="芯片位号（可重复）。不指定且无 --all 时按板上全部 IC 处理。",
)
@click.option("--all", "all_chips", is_flag=True, help="板上所有 IC 类元件逐个跑。")
@click.option(
    "--write",
    is_flag=True,
    help="通过机器闸门后真的写入 primitives/auto/ 并重建索引（默认只出报告）。",
)
@click.option(
    "--primitives-dir",
    "primitives_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="模板库目录（默认仓库 primitives/）。测试用临时目录，不动真实模板库。",
)
@click.option(
    "--fixtures-dir",
    "fixtures_dir",
    type=click.Path(path_type=Path),
    default=None,
    help="基准板目录（默认仓库 fixtures/），闸门 2/3 的回归对比范围。",
)
@click.option(
    "--format",
    "out_format",
    type=click.Choice(["text", "json"]),
    default="text",
    show_default=True,
)
def draft(
    board: str,
    chips: tuple[str, ...],
    all_chips: bool,
    write: bool,
    primitives_dir: Path | None,
    fixtures_dir: Path | None,
    out_format: str,
) -> None:
    """板 + 芯片位号 → 自动模板草稿（机器闸门 0–3 全过才写入）。"""
    from circuit_graph.primitive_autodraft import render_report, run_drafts

    result = run_drafts(
        board,
        chips=list(chips) or None,
        all_chips=all_chips,
        write=write,
        primitives_dir=primitives_dir,
        fixtures_dir=fixtures_dir,
    )

    if write:
        result["index_rebuilt"] = _rebuild_primitive_index(primitives_dir)

    report = render_report(result)
    report_path = AUTO_REPORT_PATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report, encoding="utf-8", newline="\n")

    if out_format == "json":
        payload = dict(result)
        payload["report_path"] = report_path.as_posix()
        sys.stdout.buffer.write(
            (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
        return

    lines = [
        f"板: {result['board']}  （报告: {report_path.as_posix()}）",
        "",
        _pad("位号", 8) + " " + _pad("型号", 18) + " " + _pad("state", 16)
        + " " + _pad("结论", 42) + " 耗时",
    ]
    for row in result["rows"]:
        part = (row.get("part_number") or "-")[:18]
        state = (row.get("state") or "-")[:16]
        if row.get("action") == "write":
            verdict = f"写入 {row.get('file')}"
        elif row.get("action") == "would-write":
            verdict = "闸门通过（未 --write）"
        else:
            verdict = row.get("reason") or "skip"
        lines.append(
            _pad(str(row.get("chip", "")), 8) + " " + _pad(part, 18) + " "
            + _pad(state, 16) + " " + _pad(verdict, 42) + f" {row.get('seconds')}s"
        )
    lines.append("")
    lines.append(
        f"写入 {len(result.get('written') or [])} 个 / 共 {len(result['rows'])} 颗芯片"
    )
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
