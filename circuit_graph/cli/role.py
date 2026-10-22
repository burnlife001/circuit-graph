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
            rec = db.get_component(key)
            if rec is None:
                continue
            return db.get_pinout(rec.part_number)
        return None

    return provider


@main.command(name="role")
@click.argument("designator")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
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
@click.option("--no-cache", is_flag=True, help="Bypass the IR parse/enrich cache.")
def role_cmd(
    designator: str,
    file: Path,
    out_format: str,
    db_path: Path | None,
    cache_dir: Path | None,
    no_cache: bool,
) -> None:
    """Answer "what does DESIGNATOR do" in one shot."""
    from circuit_graph.role import infer_role

    ir = _parse_and_enrich(file, cache_dir=cache_dir, use_cache=not no_cache)

    if db_path is None:
        from circuit_graph.cli.app import DEFAULT_KB_PATH

        db_path = DEFAULT_KB_PATH
    provider = _kb_pinout_provider(db_path)

    result = infer_role(ir, designator, provider)
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
        sys.stdout.buffer.write(
            (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        )
        return

    # text：答案前置 —— 第 1 行 = 位号+值+功能定性
    from circuit_graph.generators.cg_graph import _format_component_value

    comp = next((c for c in ir.components if c.id.upper() == designator.upper()), None)
    value = _format_component_value(comp) if comp else None
    head = f"{designator} {value} — " if value else f"{designator} — "
    lines = [head + result.title, f"  {result.summary}"]
    if result.evidence:
        lines.append("  拓扑: " + "；".join(result.evidence))
    sys.stdout.buffer.write(("\n".join(lines) + "\n").encode("utf-8"))
