"""nl_to_jsonl —— 把 agent 从人类自然语言整理出的答案落进 answers.jsonl 基准层。

用法：
    python .claude/skills/cg-align/scripts/nl_to_jsonl.py <net> <input.json> \
        [--answers <path>] [--dry-run] [--keep-source]

输入（agent 侧的中间格式，SKILL.md 里定义）：
    {"entries": [
      {"designator": "R95", "function": "...", "detail": "...",
       "block_hint": "可选", "source_note": "可选",
       "source": "可选(human|session-correction|cg-role)", "session": "可选"}
    ]}

脚本是纯确定性逻辑：校验位号、按权威优先级处理冲突、备份+原子写回。
不调 LLM、不联网、不改 fixtures/protel 下的基准文件（--dry-run 或 --answers 指到副本）。

source 判定（auto）：
    entry 里写了 source 就用它；没写则 —— 基准里没有该位号 → human（新答案）；
    基准里已有该位号 → session-correction（对既有答案的修订）。
    基准行 source ∈ {human, session-correction} 且本次 source ≠ human → 记冲突、拒绝覆盖、exit 1；
    想覆盖权威基准必须显式声明 human（entry 写 "source": "human" 或 --source human）。

退出码：0 = 干净；1 = 有 errors 或 conflicts（报告已打印）；2 = 用法/IO 错误。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from align_common import (  # noqa: E402
    UNMATCHED_BLOCK,
    canonical_designator,
    cg_query_all,
    default_answers_path,
    emit,
    fail,
    natural_key,
    read_answers,
    resolve_block_hint,
    template_ids,
    unmatched_strip_note_required,
    write_answers_atomic,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="把 NL 答案落进 answers.jsonl（确定性校验+冲突处理）"
    )
    ap.add_argument("net", help="网表文件（只用于查位号，不解析原文）")
    ap.add_argument("input", help="agent 整理出的 input.json")
    ap.add_argument(
        "--answers",
        default=None,
        help="基准 jsonl 路径（默认 <net 同目录>/<stem>.answers.jsonl）",
    )
    ap.add_argument(
        "--dry-run", action="store_true", help="只打印将发生的变更，不写文件"
    )
    ap.add_argument(
        "--keep-source",
        action="store_true",
        help="更新已有行时保留其原 source（新增行仍记 human）",
    )
    ap.add_argument(
        "--source",
        default=None,
        choices=["human", "session-correction", "cg-role"],
        help="强制本次全部条目的 source（默认 auto：新增行 human、改既有行 session-correction）",
    )
    return ap.parse_args(argv)


def load_input(path: Path) -> list[dict]:
    if not path.is_file():
        fail(f"输入文件不存在: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        fail(f"输入文件不是合法 JSON: {path}: {exc}")
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        fail(f"输入文件缺少 entries 数组: {path}")
    return [e for e in entries if isinstance(e, dict)]


def build_row(
    entry: dict,
    designator: str,
    comp: dict,
    old: dict | None,
    source: str,
    block: str,
) -> dict:
    """合成一行基准记录（字段顺序与既有 answers.jsonl 保持一致）。"""
    detail = entry.get("detail") or ""
    if not detail and old:
        detail = old.get("detail") or ""
    if old:
        row_type = old.get("type") or comp.get("type") or ""
        row_value = old.get("value") if old.get("value") is not None else comp.get("value")
        # confidence 属于"答案"而非"位号"：定性文本变了，旧 confidence 不得继承
        # （旧 high/user-confirmed 描述的是旧答案的支撑强度），回到来源默认值。
        same_function = (entry.get("function") or "") == (old.get("function") or "")
        inherited = old.get("confidence") if same_function else None
        confidence = (
            entry.get("confidence")
            or inherited
            or ("user-confirmed" if source == "human" else "high")
        )
    else:
        row_type = comp.get("type") or ""
        row_value = comp.get("value")
        confidence = entry.get("confidence") or (
            "user-confirmed" if source == "human" else "high"
        )
    block_instance = f"{block}-{designator.lower()}" if block else designator.lower()
    if old is not None and block == str(old.get("block") or ""):
        # block 没变 → 保留既有 block_instance（既有基准的实例命名不统一，不动它）
        block_instance = str(old.get("block_instance") or block_instance)
    row = {
        "designator": designator,
        "type": row_type,
        "value": row_value,
        "function": entry.get("function") or "",
        "detail": detail,
        "block": block,
        "block_instance": block_instance,
        "confidence": confidence,
        "source": source,
    }
    session = entry.get("session") or (old or {}).get("session")
    if session:
        row["session"] = session
    return row


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    net = Path(args.net)
    if not net.is_file():
        fail(f"网表文件不存在: {net}")

    entries = load_input(Path(args.input))
    answers_path = Path(args.answers) if args.answers else default_answers_path(net)
    known_ids = template_ids()

    rows = read_answers(answers_path)
    malformed = [r for r in rows if r.get("__malformed__")]
    if malformed:
        fail(
            f"基准文件含 {len(malformed)} 行无法解析的 JSON，"
            f"请先修复再写入: {answers_path}"
        )
    table = {r["designator"]: r for r in rows if r.get("designator")}

    comps = cg_query_all(net)
    comp_by_id = {c["id"]: c for c in comps}

    added: list[dict] = []
    updated: list[dict] = []
    conflicts: list[str] = []
    errors: list[str] = []
    processed: set[str] = set()

    for idx, entry in enumerate(entries, 1):
        raw_desig = str(entry.get("designator") or "").strip()
        if not raw_desig:
            errors.append(f"entries[{idx}] 缺少 designator")
            continue
        designator = canonical_designator(raw_desig, comp_by_id)
        if designator is None:
            errors.append(
                f"{raw_desig}：网表里没有这个位号（cg query --all 未列出）"
            )
            continue
        function = str(entry.get("function") or "").strip()
        if not function:
            errors.append(f"{designator}：缺少 function")
            continue
        if designator in processed:
            errors.append(f"{designator}：input 里重复出现，忽略后一条")
            continue
        processed.add(designator)

        source = str(entry.get("source") or "").strip()
        if source and source not in ("human", "session-correction", "cg-role"):
            errors.append(f"{designator}：非法 source {source!r}")
            continue

        old = table.get(designator)
        if not source:
            # auto：基准里没有该位号 → 新答案记 human；基准里已有 → 记 session-correction
            # （对既有答案的修订必须过冲突检查，人工确认才写 human）
            source = args.source or ("human" if old is None else "session-correction")
        if args.keep_source and old is not None:
            source = str(old.get("source") or source)

        if (
            old is not None
            and old.get("source") in ("human", "session-correction")
            and source != "human"
        ):
            conflicts.append(
                f"{designator}：基准已有 source={old.get('source')}，"
                f"新来源 {source} 无权覆盖（权威优先级 human > session-correction > cg-role）"
            )
            continue

        block = ""
        hint = entry.get("block_hint")
        if hint:
            resolved = resolve_block_hint(str(hint), known_ids)
            if resolved is None:
                errors.append(
                    f"{designator}：未知模板 id {str(hint).strip()!r}，该条目被拒"
                    f"（不会写入、也不会清空既有 block；"
                    f"确认无模板可用显式写 \"{UNMATCHED_BLOCK}\"）"
                )
                continue
            if resolved == UNMATCHED_BLOCK and unmatched_strip_note_required(
                str(old.get("block") or "") if old is not None else "",
                entry.get("source_note"),
                known_ids,
            ):
                errors.append(
                    f"{designator}：block_hint \"{UNMATCHED_BLOCK}\" 将清除既有模板归属"
                    f" {old.get('block')!r}，必须在 source_note 里写明依据"
                    f"（如走 shunt 规则路径、网表证据等）"
                )
                continue
            block = resolved
        elif old is not None:
            # 没给 hint → 保留既有 block，不拿空值覆盖基准
            block = str(old.get("block") or "")

        row = build_row(entry, designator, comp_by_id[designator], old, source, block)
        if old is None:
            added.append(row)
        else:
            updated.append(row)

    # 写回：更新项原地替换，新增项按位号自然序追加
    new_rows: list[dict] = []
    for row in rows:
        desig = row.get("designator")
        match = next((u for u in updated if u["designator"] == desig), None)
        if match is not None:
            new_rows.append(match)
        else:
            new_rows.append(row)
    for row in sorted(added, key=lambda r: natural_key(r["designator"])):
        new_rows.append(row)

    # ---- 报告
    lines = [
        "=== nl_to_jsonl {} ===".format("dry-run" if args.dry_run else "写入"),
        f"netlist : {net.as_posix()}",
        f"answers : {answers_path.as_posix()}",
        f"entries : {len(entries)}",
        "",
        f"[新增] {len(added)}",
    ]
    for row in added:
        lines.append(f"  + {row['designator']}  {row['function']}")
    lines.append(f"[更新] {len(updated)}")
    for row in updated:
        old = table.get(row["designator"]) or {}
        lines.append(
            f"  ~ {row['designator']}  {row['function']}"
            f"   (旧: {old.get('function', '')} / source={old.get('source', '-')}"
            f" → 新 source={row['source']})"
        )
    lines.append(f"[冲突] {len(conflicts)}")
    for item in conflicts:
        lines.append(f"  ! {item}")
    lines.append(f"[错误] {len(errors)}")
    for item in errors:
        lines.append(f"  x {item}")
    lines.append(
        f"统计：新增 {len(added)} / 更新 {len(updated)} / "
        f"冲突 {len(conflicts)} / 错误 {len(errors)}；"
        f"基准行数 {len(rows)} → {len(new_rows)}"
    )

    if not args.dry_run:
        backup = write_answers_atomic(answers_path, new_rows)
        lines.append(f"已写回 {answers_path.as_posix()}（备份 {backup.as_posix()}）")

    emit("\n".join(lines) + "\n")

    if conflicts or errors:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
