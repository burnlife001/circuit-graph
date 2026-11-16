"""coverage_test —— 全量对齐测试：当前 cg 定性 vs 基准 answers.jsonl。

用法：
    python .claude/skills/cg-align/scripts/coverage_test.py <net> \
        [--answers <path>] [--json] [--threshold N]

流程（全部经 cg，脚本不读 .NET 原文）：
    cg role --all NET --format json          → 当前 function（title）与当前 block（rule）
    cg primitives coverage NET --format json → 覆盖度汇总

block 以 role --all 的 rule 为准：``primitive:<实例>`` / ``primitive-live:<实例>``
两条路径都覆盖（coverage 命令只统计识别器产出的块，洪泛降级的 live-match
命中会被它漏算，所以不能用它做 block 判据）。

报告分节：aligned / refined / mismatched / unmatched-in-answers / coverage，
外加 block-drift（基准 block 与当前 block 不同）与多余基准（answers 里网表已无此位号）。

function 比较分三类（classify_alignment，详见 align_common）：
- aligned：两边剥掉 title 尾部 " · 角色" 后缀后文本相同；
- refined：文本不同但基准 block 与当前模板 id 相同——同一模板/功能，
  当前只是补充网络、配对元件、型号、参数等证据。只认模板 id 这一
  强结构证据，不以文本包含关系认定语义相同；
- mismatched：其余全部——block 不同（功能换了模板，如 R2 下拉→采样）、
  任一侧 block 未知（(unmatched)）。refined 是"待 agent 复核"归类，
  报告逐条列出供依据模板语义与用户答案确认，同义措辞不自动判对。

结论行 ``对齐率 X/Y (Z%)``：X = aligned，Y = 基准里有的元件数，Z = X/Y*100；
另报 ``有效对齐率 (含 refined)`` = (aligned+refined)/Y——refined 不算
真实分歧，但需 agent 过目，不与 aligned 混同。
退出码：0 = 报告正常产出（报告本身不算失败）；1 = 低于 --threshold；2 = 运行错误。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from align_common import (  # noqa: E402
    UNMATCHED_BLOCK,
    cg_coverage,
    cg_role_all,
    classify_alignment,
    default_answers_path,
    emit,
    fail,
    natural_key,
    read_answers,
    template_ids,
    template_of_instance,
)

_RULE_RE = re.compile(r"^(?:primitive|primitive-live):(.+)$")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="当前 cg 定性 vs 基准 answers.jsonl 的全量 diff")
    ap.add_argument("net", help="网表文件")
    ap.add_argument("--answers", default=None, help="基准 jsonl 路径")
    ap.add_argument("--json", action="store_true", help="输出 JSON（机读）")
    ap.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="对齐率百分比下限；低于该值 exit 1（供 CI）",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    net = Path(args.net)
    if not net.is_file():
        fail(f"网表文件不存在: {net}")
    answers_path = Path(args.answers) if args.answers else default_answers_path(net)

    rows = read_answers(answers_path)
    if not rows:
        fail(f"基准文件不存在或为空: {answers_path}")
    baseline = {r["designator"]: r for r in rows if r.get("designator")}

    roles = cg_role_all(net)
    if not roles:
        fail(f"cg role --all 没有返回结果: {net}")
    coverage = cg_coverage(net)

    current = {r["designator"]: r for r in roles}
    # block 判据以 role --all 的 rule 为准：primitive:（块命中）与
    # primitive-live:（flood 降级后的现场匹配）两条路径都要算命中；
    # coverage 命令的块归组会漏掉 live-match，只用于覆盖汇总。
    ids = template_ids()
    current_block: dict[str, str] = {}
    for r in roles:
        m = _RULE_RE.match(r.get("rule", "") or "")
        tid = template_of_instance(m.group(1), ids) if m else None
        current_block[r["designator"]] = tid or UNMATCHED_BLOCK

    aligned: list[dict] = []
    refined: list[dict] = []
    mismatched: list[dict] = []
    block_drift: list[dict] = []

    for desig in sorted(set(baseline) & set(current), key=natural_key):
        base = baseline[desig]
        row = {
            "designator": desig,
            "baseline_function": base.get("function", ""),
            "baseline_source": base.get("source", ""),
            "current_title": current[desig].get("title", ""),
        }

        base_block = str(base.get("block") or UNMATCHED_BLOCK)
        cur_block = current_block.get(desig, UNMATCHED_BLOCK)
        verdict = classify_alignment(
            base.get("function"),
            base_block,
            current[desig].get("title"),
            cur_block,
        )
        if verdict == "aligned":
            aligned.append(row)
        else:
            row["block"] = cur_block
            if verdict == "refined":
                refined.append(row)
            else:
                mismatched.append(row)

        if base_block != cur_block:
            block_drift.append(
                {
                    "designator": desig,
                    "baseline_block": base_block,
                    "current_block": cur_block,
                }
            )

    unmatched_in_answers = [
        {
            "designator": desig,
            "current_title": current[desig].get("title", ""),
        }
        for desig in sorted(set(current) - set(baseline), key=natural_key)
    ]
    stale_in_answers = sorted(set(baseline) - set(current), key=natural_key)

    total = len(aligned) + len(refined) + len(mismatched)
    rate = round(len(aligned) * 100.0 / total, 1) if total else 0.0
    effective = len(aligned) + len(refined)
    eff_rate = round(effective * 100.0 / total, 1) if total else 0.0

    totals = coverage.get("totals", {})
    coverage_summary = {
        "components": totals.get("components", len(current)),
        "matched": totals.get("matched", len(current_block) - len(coverage.get("unmatched", []))),
        "unmatched": totals.get("unmatched", len(coverage.get("unmatched", []))),
        "templates": totals.get("templates", len(coverage.get("templates", []))),
        "unmatched_designators": coverage.get("unmatched", []),
    }

    if args.json:
        payload = {
            "netlist": net.as_posix(),
            "answers": answers_path.as_posix(),
            "summary": {
                "aligned": len(aligned),
                "refined": len(refined),
                "mismatched": len(mismatched),
                "total": total,
                "rate_percent": rate,
                "effective_rate_percent": eff_rate,
                "threshold": args.threshold,
            },
            "aligned": aligned,
            "refined": refined,
            "mismatched": mismatched,
            "unmatched_in_answers": unmatched_in_answers,
            "stale_in_answers": stale_in_answers,
            "block_drift": block_drift,
            "coverage": coverage_summary,
        }
        emit(json.dumps(payload, indent=2, ensure_ascii=False) + "\n")
    else:
        lines = [
            "=== coverage_test ===",
            f"netlist : {net.as_posix()}",
            f"answers : {answers_path.as_posix()}",
            "",
            f"[aligned] {len(aligned)}",
        ]
        for row in aligned:
            lines.append(f"  = {row['designator']}  {row['current_title']}")
        lines.append(f"[refined] {len(refined)}  （同一模板，当前补充网络/配对/型号/参数；待 agent 复核）")
        for row in refined:
            lines.append(
                f"  ~ {row['designator']}  基准[{row['baseline_source']}]"
                f": {row['baseline_function']}  |  当前: {row['current_title']}"
                f"  (block={row['block']})"
            )
        lines.append(f"[mismatched] {len(mismatched)}")
        for row in mismatched:
            lines.append(
                f"  ! {row['designator']}  基准[{row['baseline_source']}]"
                f": {row['baseline_function']}  |  当前: {row['current_title']}"
            )
        lines.append(f"[unmatched-in-answers] {len(unmatched_in_answers)}")
        for row in unmatched_in_answers:
            lines.append(f"  ? {row['designator']}  {row['current_title']}")
        lines.append(f"[block-drift] {len(block_drift)}")
        for row in block_drift:
            lines.append(
                f"  ~ {row['designator']}  基准 block={row['baseline_block']}"
                f" → 当前 block={row['current_block']}"
            )
        lines.append(f"[stale-in-answers] {len(stale_in_answers)}")
        for desig in stale_in_answers:
            lines.append(f"  - {desig}  基准里有、网表里已无")
        lines.append(
            f"[coverage] 元件 {coverage_summary['components']}，"
            f"命中模板 {coverage_summary['matched']}"
            f"（{coverage_summary['templates']} 个模板），"
            f"未命中 {coverage_summary['unmatched']}"
        )
        lines.append(f"对齐率 {len(aligned)}/{total} ({rate}%)")
        lines.append(f"有效对齐率(含 refined) {effective}/{total} ({eff_rate}%)")
        emit("\n".join(lines) + "\n")

    if args.threshold is not None and rate < args.threshold:
        emit(f"对齐率 {rate}% 低于阈值 {args.threshold}%\n")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
