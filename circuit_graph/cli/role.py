"""cg role —— 一条命令回答 "X 是干什么的"。

    cg role R100 board.NET            # 功能定性（答案前置）
    cg role R100 board.NET --format json

推理在 circuit_graph.role.infer_role；引脚功能从 KB 取
（cg kb lookup 同源），KB 缺失时该 IC 引脚按 unknown 降级。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click

from circuit_graph.cli.app import _parse_and_enrich, main
from circuit_graph.ir import Component


def _kb_pinout_provider(kb_path: Path):
    """把 KB 查 pinout 适配成 role 引擎的 provider；KB 打不开时全部 unknown。"""
    db = None
    try:
        from circuit_graph.knowledge.db import ComponentDatabase

        db = ComponentDatabase.open(kb_path)
    except Exception:  # noqa: BLE001 —— KB 不可用时降级，不阻断定性
        db = None

    def provider(comp: Component) -> dict[str, str] | None:
        if db is None:
            return None
        for key in (comp.part_number, comp.raw_value):
            if not key:
                continue
            # 精确行可能是不带任何数据的板级变体（MM32F014X-QFP48，0 引脚），
            # get_pinout_effective 沿 精确→最长前缀 链取第一个真正带引脚数据的行。
            po = db.get_pinout_effective(key)
            if po:
                return po
        return None

    return provider


def _kb_facts_provider(kb_path: Path):
    """把 KB 的器件事实（category/subcategory）适配成 role 引擎的 facts 通道。

    KB 打不开或查不到时返回空 dict——定性规则须在没有事实时如实降级。
    """
    db = None
    try:
        from circuit_graph.knowledge.db import ComponentDatabase

        db = ComponentDatabase.open(kb_path)
    except Exception:  # noqa: BLE001 —— KB 不可用时降级，不阻断定性
        db = None

    def provider(comp: Component) -> dict[str, str]:
        if db is None:
            return {}
        for key in (comp.part_number, comp.raw_value):
            if not key:
                continue
            # 与 _kb_pinout_provider 同一策略：变体行无事实时沿前缀链降级。
            for rec in db.lookup_candidates(key):
                facts = {}
                for field in ("category", "subcategory"):
                    value = getattr(rec, field, None)
                    if value:
                        facts[field] = str(value)
                if facts:
                    return facts
        return {}

    return provider


@main.command(name="role")
@click.argument("designator", required=False)
@click.argument(
    "file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=False,
)
@click.option(
    "--all",
    "all_components",
    is_flag=True,
    help="对文件里每个元件都做定性（一次解析，全量输出）。",
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
    help="KB SQLite path (default: <pkg>/knowledge/circuit_graph.db)。",
)
@click.option(
    "--cache-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="IR cache directory (default: <repo>/.cg-cache)。",
)
@click.option(
    "--kb-source",
    "kb_source",
    default=None,
    help=(
        "KB 补全来源：pin2json | datasheet | http(s)://…（datasheet PDF）。"
        "不传 = 严格离线，只读现有 KB。"
    ),
)
@click.option("--no-cache", is_flag=True, help="Bypass the IR parse/enrich cache.")
def role_cmd(
    designator: str | None,
    file: Path | None,
    all_components: bool,
    out_format: str,
    db_path: Path | None,
    cache_dir: Path | None,
    kb_source: str | None,
    no_cache: bool,
) -> None:
    """Answer "what does DESIGNATOR do" in one shot.

    ``cg role --all FILE`` 一次解析、对每个元件定性（全量）。

    ``--kb-source`` 显式指定 KB 补全来源；不传时严格离线（不联网、不调外部 CLI）。
    """
    from circuit_graph.role import infer_role

    designator, file = _resolve_target(designator, file, all_components)

    source = None
    if kb_source is not None:
        from circuit_graph.knowledge.kb_source import KbSource, KbSourceError

        try:
            source = KbSource.parse(kb_source)
        except KbSourceError as exc:
            click.echo(f"error: {exc}", err=True)
            sys.exit(2)
        if source.is_url and all_components:
            click.echo(
                "error: --kb-source <URL> 只能用于单个位号：一份 datasheet PDF "
                "只对应一个型号，无法批量套用到整块板。",
                err=True,
            )
            sys.exit(2)

    ir = _parse_and_enrich(file, cache_dir=cache_dir, use_cache=not no_cache)

    if db_path is None:
        from circuit_graph.cli.app import DEFAULT_KB_PATH

        db_path = DEFAULT_KB_PATH

    fill_report = None
    if source is not None:
        fill_report = _fill_kb(ir, source, designator, all_components, db_path, cache_dir)
        _echo_fill_report(fill_report)

    provider = _kb_pinout_provider(db_path)

    if all_components:
        _emit_all_roles(
            ir, provider, out_format, file, fill_report=fill_report, kb_path=db_path
        )
        return

    from circuit_graph.role import LiveMatchSession

    # session 与 provider 用同一个 KB：--kb-source 刚补进去的引脚名，
    # 同一次 role 就要能被模板匹配看见。
    session = LiveMatchSession(ir, kb_path=db_path)
    result = infer_role(
        ir,
        designator,
        provider,
        live_session=session,
        facts_provider=_kb_facts_provider(db_path),
    )
    if result is None:
        click.echo(f"error: component not found: {designator}", err=True)
        sys.exit(1)

    if out_format == "json":
        payload = {
            "designator": designator,
            "rule": result.rule,
            "title": result.title,
            "summary": result.summary,
            "confidence": result.confidence,
            "evidence": result.evidence,
            "data": result.data,
        }
        if fill_report is not None:
            payload["kb_fill"] = fill_report.to_dict()
        sys.stdout.buffer.write(
            (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
        return

    # text：答案前置 —— 第 1 行 = 位号+值+功能定性
    from circuit_graph.generators.cg_graph import _format_component_value

    comp = next((c for c in ir.components if c.id.upper() == designator.upper()), None)
    value = _format_component_value(comp) if comp else None
    head = f"{designator} {value} — " if value else f"{designator} — "
    title = result.title
    ic_context = result.data.get("ic")
    if ic_context and ic_context not in title:
        title = f"{title}（{ic_context}）"
    lines = [head + title, f"  {result.summary}"]
    if result.evidence:
        lines.append("  拓扑: " + "；".join(result.evidence))
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))


def _resolve_target(
    designator: str | None,
    file: Path | None,
    all_components: bool,
) -> tuple[str, Path]:
    """解析位置参数：``cg role R100 FILE`` / ``cg role --all FILE``。"""
    if all_components:
        if file is not None:
            click.echo("error: --all cannot be combined with DESIGNATOR", err=True)
            sys.exit(2)
        if designator is None:
            click.echo("error: --all requires a netlist file", err=True)
            sys.exit(2)
        target = Path(designator)
        if not target.is_file():
            click.echo(f"error: not a netlist file: {designator}", err=True)
            sys.exit(2)
        return "", target

    if designator is None:
        click.echo("error: missing argument: DESIGNATOR", err=True)
        sys.exit(2)
    if file is None:
        click.echo("error: missing argument: FILE", err=True)
        sys.exit(2)
    return designator, file


def _fill_kb(ir, source, designator, all_components, db_path, cache_dir):
    """按显式来源补 KB 里缺失的引脚（单个失败不阻断其他元件）。

    没有可补全的目标（单位号是被动件/无型号/板内查无此位号，或 ``--all``
    时板内没有带型号的非被动器件）时返回带说明的空 :class:`FillReport`，
    让调用方照常走后续定性流程——不得因返回 None 崩掉。
    """
    from circuit_graph.knowledge.db import ComponentDatabase
    from circuit_graph.knowledge.kb_source import (
        FillReport,
        fill_missing_pins,
        target_components,
    )

    report = FillReport(source=source.describe())
    components = target_components(ir, designator, all_components=all_components)
    if not components:
        report.note = (
            "没有可补全的目标：单位号目标须是非被动且有型号的器件"
            "（被动件/无型号/板上查无此位号不补）；--all 需要板内有带型号的"
            "非被动器件"
        )
        return report
    db = ComponentDatabase.open(db_path)
    try:
        return fill_missing_pins(
            db,
            components,
            source,
            cache_dir=(Path(cache_dir) if cache_dir else None),
        )
    finally:
        db.close()


def _echo_fill_report(report) -> None:
    """KB 补全结果写 stderr（不污染 stdout 的答案 / JSON 输出）。"""
    click.echo(report.summary(), err=True)
    for row in report.resolved:
        click.echo(
            f"  + {row['designator']} ({row['part_number']}): "
            f"{row['pin_count']} pins from {row['pin_source']}",
            err=True,
        )
    for row in report.failed:
        click.echo(
            f"  ! {row['designator']} ({row['part_number']}): {row['reason']}",
            err=True,
        )
    for row in report.skipped:
        click.echo(f"  = {row['designator']}: {row['reason']}", err=True)


def _emit_all_roles(
    ir, provider, out_format: str, file: Path, fill_report=None, kb_path=None
) -> None:
    """全量定性：一次解析 + 一份共享的现场匹配上下文。"""
    from circuit_graph.role import LiveMatchSession, infer_role

    session = LiveMatchSession(ir, kb_path=kb_path)
    facts_provider = _kb_facts_provider(kb_path) if kb_path is not None else None
    results: list[dict] = []
    for comp in ir.components:
        result = infer_role(
            ir, comp.id, provider, live_session=session, facts_provider=facts_provider
        )
        if result is None:
            continue
        results.append(
            {
                "designator": comp.id,
                "rule": result.rule,
                "title": result.title,
                "summary": result.summary,
                "confidence": result.confidence,
                "evidence": result.evidence,
                "data": result.data,
            }
        )

    if out_format == "json":
        payload = {
            "netlist": file.as_posix(),
            "count": len(results),
            "results": results,
        }
        if fill_report is not None:
            payload["kb_fill"] = fill_report.to_dict()
        sys.stdout.buffer.write(
            (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
        return

    # text：与单元件模式同款 —— 位号+值+功能定性在前，摘要第二行
    from circuit_graph.generators.cg_graph import _format_component_value

    values = {c.id: _format_component_value(c) for c in ir.components}
    lines: list[str] = []
    for r in results:
        value = values.get(r["designator"])
        head = f"{r['designator']} {value} — " if value else f"{r['designator']} — "
        lines.append(head + r["title"])
        lines.append(f"  {r['summary']}")
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
