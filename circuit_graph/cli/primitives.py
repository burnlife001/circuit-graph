"""cg primitives —— primitives 模板工具组。

    cg primitives coverage board.NET               # 每个元件命中了哪个模板
    cg primitives coverage board.NET --format json

coverage 从 ``ir.blocks`` 归组：block.id 形如 ``<模板id>-<实例后缀>``，
模板 id 用**最长前缀匹配**从 ``circuit_graph.primitives.load_primitives()``
的模板 id 集合里解出（"rc-snubber" 与 "rc-snubber-series" 同时命中时取长的）。
每个元件只归一个组：块内去重，跨块重复出现归第一个遇到的**模板**块
（认不出模板的块不参与归组，其成员仍可被后面的模板块收走）。

注意：本命令统计的是"识别器产出的功能块"覆盖度，不是"模板匹配"覆盖度 ——
识别器因洪泛降级而只留候选、不留块的元件会算作未命中。
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import click

from circuit_graph.cli.app import (
    _parse_and_enrich,
    build_pin_category_provider,
    main,
)

_NATURAL_RE = re.compile(r"(\d+)")
_MAX_UNMATCHED_SHOWN = 20


def _natural_key(text: str) -> tuple:
    """位号自然序：R2 < R10（字母段按字典序，数字段按数值）。"""
    parts = [p for p in _NATURAL_RE.split(text) if p != ""]
    return tuple((1, int(p), "") if p.isdigit() else (0, 0, p) for p in parts)


def _template_of_block(block_id: str, template_ids: list[str]) -> str | None:
    """用最长前缀匹配从 block.id 解出模板 id。

    前缀必须落在分隔符（``-``）或字符串结尾上，否则 "rc-low" 会吃掉
    "rc-low-pass-x" 里的 "rc-low" 那一类误配。
    """
    best: str | None = None
    for tid in template_ids:
        if not block_id.startswith(tid):
            continue
        rest = block_id[len(tid):]
        if rest and not rest.startswith("-"):
            continue
        if best is None or len(tid) > len(best):
            best = tid
    return best


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
    for block in ir.blocks:
        tid = _template_of_block(block.id, template_ids)
        if tid is None:
            continue
        for cid in block.component_ids:
            if cid not in known_components or cid in assigned:
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
