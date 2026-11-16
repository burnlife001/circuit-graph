"""propose_template —— 为未命中的元件生成"新模板"草稿（只打印，不写文件）。

用法：
    python .claude/skills/cg-align/scripts/propose_template.py <net> <designators...> \
        [--function "整体功能描述"]

流程（确定性，只经 cg 取电路数据）：
    cg query NET --all --format json          → 校验位号 + 取元件类型
    cg query NET --comp D1 --comp D2 ...       → 引脚→网络
    共性网络聚类 → signals 候选
    primitives/schema.json + 同类现有模板      → 格式参照

输出：草稿 JSON + 参照模板摘要 + checklist（注释形式）。不调 LLM、不联网、不写文件。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from align_common import (  # noqa: E402
    cg_query_all,
    cg_query_comps,
    emit,
    fail,
    load_templates,
)

# cg query 的 type（小写 device kind）→ 模板里的 component type 写法
TYPE_BY_DEVICE_KIND = {
    "resistor": "Resistor",
    "capacitor": "Capacitor",
    "inductor": "Inductor",
    "ic": "IC",
    "mosfet": "Mosfet",
    "diode": "Diode",
    "bridge_rectifier": "BridgeRectifier",
    "connector": "Connector",
    "unknown": "Unknown",
}

# 保守默认打分：与既有模板一致
DEFAULT_SCORING = {
    "weights": {
        "structural": 0.35,
        "naming": 0.25,
        "parametric": 0.20,
        "type": 0.20,
    },
    "thresholds": {"high": 0.80, "medium": 0.50, "low": 0.0},
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="生成新 primitives 模板草稿（不写文件）")
    ap.add_argument("net", help="网表文件")
    ap.add_argument("designators", nargs="+", help="待建模板的位号")
    ap.add_argument("--function", default="", help="整体功能描述（用作模板 name）")
    return ap.parse_args(argv)


def slugify(text: str) -> str:
    """ASCII slug：只留 a-z0-9-，其余转 '-'。中文会被丢掉。"""
    lowered = text.strip().lower()
    out = re.sub(r"[^a-z0-9]+", "-", lowered)
    return out.strip("-") or "template"


def signal_id(net_name: str) -> str:
    return "net_" + re.sub(r"[^a-z0-9]+", "_", net_name.strip().lower()).strip("_")


def pick_references(templates: list[dict], target_types: set[str]) -> list[dict]:
    """按元件类型挑 2-3 个同类现有模板做格式参照。"""
    scored: list[tuple[int, str, dict]] = []
    for tpl in templates:
        types: set[str] = set()
        for comp in tpl.get("components", []):
            ctype = comp.get("type")
            for item in ctype if isinstance(ctype, list) else [ctype]:
                if isinstance(item, str):
                    types.add(item)
        overlap = len(types & target_types)
        if overlap:
            scored.append((overlap, str(tpl.get("id", "")), tpl))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [tpl for _, _, tpl in scored[:3]]


def template_summary(tpl: dict) -> str:
    sig_ids = [str(s.get("id")) for s in tpl.get("signals", [])]
    comp_parts = []
    for comp in tpl.get("components", []):
        ctype = comp.get("type")
        if isinstance(ctype, list):
            ctype = "/".join(str(t) for t in ctype)
        pins = ",".join(str(p.get("number")) for p in comp.get("pins", []))
        comp_parts.append(f"{comp.get('id')}({ctype})[{pins}]")
    return f"signals: {', '.join(sig_ids) or '-'} | components: {'; '.join(comp_parts) or '-'}"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    net = Path(args.net)
    if not net.is_file():
        fail(f"网表文件不存在: {net}")
    wanted = [d.strip() for d in args.designators if d.strip()]
    if not wanted:
        fail("至少给一个位号")

    comps = cg_query_all(net)
    comp_by_id = {c["id"]: c for c in comps}
    missing = [d for d in wanted if d not in comp_by_id]
    if missing:
        fail(f"网表里没有这些位号: {', '.join(missing)}")

    sections = cg_query_comps(net, wanted)
    pin_nets: dict[str, dict[str, str]] = {}
    pin_names: dict[str, dict[str, str]] = {}
    comp_meta: dict[str, dict] = {}
    for section in sections:
        cid = section["id"]
        pin_nets[cid] = section.get("nets", {}) or {}
        pin_names[cid] = {
            str(p.get("pin")): (p.get("name") or "")
            for p in section.get("pins", []) or []
        }
        comp_meta[cid] = comp_by_id.get(cid, {})

    # ---- 共性网络聚类
    net_to_comps: dict[str, list[str]] = {}
    for cid in wanted:
        for net_name in pin_nets.get(cid, {}).values():
            if not net_name:
                continue
            net_to_comps.setdefault(net_name, []).append(cid)
    shared = {n: cs for n, cs in net_to_comps.items() if len(set(cs)) >= 2}

    # ---- signals 候选：每个涉及到的网络一条 signal，共网排前面
    signals = []
    for net_name in sorted(shared) + sorted(set(net_to_comps) - set(shared)):
        signals.append(
            {
                "id": signal_id(net_name),
                "type": "net",
                "name_pattern": "^" + re.escape(net_name) + "$",
                "required": True,
            }
        )

    # ---- components：按实际引脚填
    components = []
    for cid in wanted:
        meta = comp_meta.get(cid, {})
        dev_kind = str(meta.get("type", "unknown")).lower()
        comp_type = TYPE_BY_DEVICE_KIND.get(dev_kind, "Unknown")
        pins = []
        for pin_number, net_name in pin_nets.get(cid, {}).items():
            if not net_name:
                continue
            label = pin_names.get(cid, {}).get(str(pin_number), "") or str(pin_number)
            pins.append({"number": str(label), "net": signal_id(net_name)})
        components.append(
            {
                "id": cid,
                "type": comp_type,
                "required": True,
                "pins": pins,
            }
        )

    # ---- 参照模板
    target_types = {str(c.get("type")) for c in components}
    references = pick_references(load_templates(), target_types)

    # ---- 草稿
    function = args.function.strip()
    name = function or f"{'+'.join(wanted)} 组成的电路（待人审命名）"
    slug_source = function if re.search(r"[a-zA-Z]", function) else ""
    slug_parts = [slugify(slug_source)] if slug_source else []
    slug_parts.append("-".join(wanted).lower())
    draft = {
        "$schema": "./schema.json",
        "id": "draft-" + "-".join(p for p in slug_parts if p),
        "name": name,
        "description": (
            f"{function or '未命名功能'}（草稿：由 propose_template.py 依据 "
            f"{net.as_posix()} 的 {', '.join(wanted)} 生成，拓扑待人审）"
        ),
        "version": "0.1.0",
        "signals": signals,
        "components": components,
        "constraints": {
            "min_components": len(components),
            "exact_component_count": False,
        },
        "scoring": DEFAULT_SCORING,
    }

    lines = [
        "=== propose_template 草稿 ===",
        f"netlist : {net.as_posix()}",
        f"target  : {', '.join(wanted)}",
        f"共性网络（≥2 元件共用）：{', '.join(sorted(shared)) or '-'}",
        "",
        "# ---- 草稿 JSON（写到 primitives/<类目>/<id>.json 之前先完成 checklist）----",
        json.dumps(draft, indent=2, ensure_ascii=False),
        "",
        "# ---- 参照模板（同类元件，格式参照）----",
    ]
    if references:
        for tpl in references:
            lines.append(
                f"# {tpl.get('id')}  ({tpl.get('__file__')})  name={tpl.get('name')}"
            )
            lines.append(f"#   {template_summary(tpl)}")
    else:
        lines.append("# （没有同类型模板可参照）")
    lines += [
        "",
        "# ---- checklist ----",
        f"# ① 人审 id（现为 {draft['id']}）、name、每个 signal 的 name_pattern 语义",
        "# ② 补反例约束：expected_role / expected_ic_pin / expected_pin_category（防误报优先）",
        "# ③ 写到 primitives/<类目>/ 后重建索引：python scripts/build_primitive_index.py",
        "# ④ 补 fixture CASE：tests/test_primitives_on_fixtures.py（每个模板至少一条 CASE）",
        "# ⑤ 全量 pytest 无新增失败（基线 12 failed / 737 passed）",
    ]
    emit("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
