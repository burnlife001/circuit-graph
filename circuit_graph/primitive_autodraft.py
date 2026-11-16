"""Automatic primitive templates — board + chip → machine-gated library entry.

自动模板（无人审流程）：板 + 芯片位号 → 自动推导模板草稿 → **机器闸门** 0–3
全部通过才写入 ``primitives/auto/<型号slug>.json``；任一闸门失败只记录原因、
不写文件。人工模板永远优先（见
``circuit_graph.enrichers.topology_recognizer``）。

为什么这样剪枝（实测数据见 ``docs/architecture.md``）：

* **成员不再绑定参考板实测值**。芯片仍按型号严格匹配（``strict_value``）；
  成员只带"值类型正则"（容值单位 / 阻值 / 感值）+ 一个数量级区间（默认 ±1 档）。
  参考板上实测值为空（NC）或无法解析的成员直接不进模板。
* **模板规模有上限**。``.*`` 网模式让 VF2 的搜索空间随"可选组数"指数膨胀：
  芯片 + 1 个可选组 0.16s、+2 组 0.74s、+3 组 9.2s、+4 组直接超时。
  所以自动模板只留 ≤2 个可选组（变体数 2**N），回放时间另有 3s 预算，
  超预算判 needs_review 不入库（模板进了库就会在每块板上跑一遍）。
* **私有网加 ``expected_ic_pin``**。成员独占网（参考板上不落任何 IC 脚）
  声明 ``forbidden``，缩小候选网集合；参考板上确实接了 IC 的网不加。

本模块只依赖仓库已有的解析器（Protel），不手写 .NET 解析。
"""

from __future__ import annotations

import json
import math
import re
import shutil
import tempfile
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from circuit_graph.ir import (
    CapacitanceValue,
    CircuitIR,
    Component,
    InductanceValue,
    ResistanceValue,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PRIMITIVES_DIR = REPO_ROOT / "primitives"
AUTO_SUBDIR = "auto"
FIXTURES_DIR = REPO_ROOT / "fixtures"

# ---------------------------------------------------------------------------
# 规模上限（防止 `.*` 网模式把 VF2 搜索空间撑爆）
# ---------------------------------------------------------------------------

#: 模板里最多保留多少个可选组（每组 2 选 1 → 变体数 2**N）。
#:
#: VF2 的搜索代价随"可选单元数"**指数增长**（实测：芯片 + 1 个可选组 0.16s，
#: +2 组 0.74s，+3 组 9.2s，+4 组直接超时）。可选组是模板里唯一能整体
#: 存在/缺席的单元，所以组数就是变体数——必须压到 2 以内才能保证回放秒级。
AUTO_MAX_GROUPS = 2
#: 模板里最多保留多少个成员（芯片本体不计入）。
AUTO_MAX_MEMBERS = 8
#: 芯片本体最多保留多少个引脚（超出的板级大芯片直接放弃自动模板）。
AUTO_MAX_CHIP_PINS = 16
#: 网上成员多于这个数视为总线/电源轨：只出信号，不出成员。
AUTO_MEMBER_CAP = 8
#: 回放命中的元件多于这个数视为"模板吞掉了整块板"。
AUTO_FLOOD_CAP = 50
#: 单次回放里**单模板匹配**的时间预算（秒）。模板进了库就会在**每块板**上跑一遍，
#: 所以太慢的模板等于拖慢整个识别器——超预算直接判 needs_review 不入库。
AUTO_MATCH_TIMEOUT_SECONDS = 2.0

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

DEFAULT_SCORING = {
    "weights": {
        "structural": 0.35,
        "naming": 0.25,
        "parametric": 0.20,
        "type": 0.20,
    },
    "thresholds": {"high": 0.80, "medium": 0.50, "low": 0.0},
}

_GND_LIKE_NETS = {"GND", "CGND", "PGND", "AGND", "DGND", "VSS"}
_GND_NET_PATTERN = r"^(GND|CGND|PGND|AGND|DGND|VSS)$"
_BOARD_NET_RE = re.compile(r"^Net[A-Za-z]*\d+_\d+$")
_AUTO_SUFFIX_BY_KIND = {
    "capacitor": "cap",
    "resistor": "res",
    "inductor": "ind",
    "diode": "diode",
    "ic": "ic",
    "mosfet": "fet",
    "bridge_rectifier": "bridge",
    "connector": "conn",
    "unknown": "part",
}


class AutoDraftError(Exception):
    """自动草稿阶段的致命错误（单颗芯片失败不影响其它芯片）。"""


# ---------------------------------------------------------------------------
# 值类型正则：容值单位 / 阻值 / 感值 + 数量级区间
# ---------------------------------------------------------------------------

#: 值族 → 单位 → SI 换算系数（单位键小写，匹配时大小写不敏感）。
_VALUE_FAMILIES: dict[str, dict[str, float]] = {
    "cap": {"pf": 1e-12, "nf": 1e-9, "uf": 1e-6, "µf": 1e-6, "μf": 1e-6},
    "res": {"ω": 1.0, "kω": 1e3, "mω": 1e6},
    "ind": {"h": 1.0, "mh": 1e-3, "uh": 1e-6, "µh": 1e-6, "μh": 1e-6, "nh": 1e-9},
}

#: IR 归一化单位 → 值族单位键。
#:
#: IR 里的单位大小写不统一（``ValueNormalizer`` 把容值/感值小写成 ``nf``/``nh``，
#: 阻值却保持 ``Ω``/``kΩ``），所以这里按"小写 + µ/μ 归一"后的键查表。
_UNIT_KEYS = {
    "ω": "ω", "kω": "kω", "mω": "mω",
    "pf": "pf", "nf": "nf", "uf": "uf", "µf": "uf",
    "nh": "nh", "uh": "uh", "µh": "uh", "mh": "mh", "h": "h",
}


def _unit_key(unit: str) -> str | None:
    """单位文本 → 值族单位键（大小写不敏感，µ/μ 等价）。"""
    key = str(unit or "").strip().lower().replace("μ", "µ")
    return _UNIT_KEYS.get(key)

#: 原始值文本兜底解析（IR 没归一化出数值时用）。
_RAW_RES_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([kKmMrR])")
_RAW_CAP_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(pf|nf|uf|µf|μf|f)\b", re.IGNORECASE)
_RAW_IND_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(nh|uh|µh|μh|mh|h)\b", re.IGNORECASE)

#: 0Ω 跳线的值类型正则（"0R-1206" / "0Ω" 都命中；"0.05R" 不命中）。
_ZERO_OHM_PATTERN = r"^\s*0+(?:\.0+)?\s*(?:[rRΩ]|ohm)\b"


def _int_chunk_regex(a: int, b: int) -> str | None:
    """Regex for the integers in ``[a, b]`` when both have the same digit count."""
    if a > b:
        return None
    if a == b:
        return str(a)
    sa, sb = str(a), str(b)
    if len(sa) != len(sb):
        return None
    if sa[0] == sb[0]:
        return "[" + sa[0] + "-" + sb[0] + "]" + (r"\d" * (len(sa) - 1))
    return "[" + sa[0] + "-9]" + (r"\d" * (len(sa) - 1))


def _int_range_regex(a: int, b: int) -> str | None:
    """Regex for every integer in ``[a, b]``（跨十进制档时拆成多段）。"""
    alts: list[str] = []
    x = a
    while x <= b:
        y = min(b, 10 ** len(str(x)) - 1)
        chunk = _int_chunk_regex(x, y)
        if chunk:
            alts.append(chunk)
        x = y + 1
    if not alts:
        return None
    return alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"


def _number_range_regex(lo: float, hi: float, ref: float) -> str | None:
    """Regex for numbers in ``[lo, hi]``（整数档 + 参考值的小数形式）。"""
    alts: list[str] = []
    first = max(1, math.ceil(lo - 1e-9))
    last = math.floor(hi + 1e-9)
    if first <= last:
        chunk = _int_range_regex(first, last)
        if chunk:
            alts.append(chunk)
    if abs(ref - round(ref)) > 1e-9 and lo <= ref <= hi:
        alts.append(str(int(ref)) + r"\.\d")
    if not alts:
        return None
    return alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"


def band_value_pattern(
    magnitude: float,
    unit_key: str,
    family: str,
    *,
    decades: float = 1.0,
) -> str | None:
    """值类型正则：同一值族内 ``magnitude ± decades`` 个数量级。

    例：``100nF`` → 允许 10nF–1uF（跨 pF/nF/µF 三种单位写法）。
    """
    mults = _VALUE_FAMILIES[family]
    if unit_key not in mults:
        return None
    base = magnitude * mults[unit_key]
    lo = base / (10.0**decades)
    hi = base * (10.0**decades)
    alts: list[str] = []
    for unit, mult in mults.items():
        nlo = lo / mult
        nhi = hi / mult
        if nhi < 0.01 or nlo > 1e7:
            continue
        nlo = max(nlo, 0.01)
        chunk = _number_range_regex(nlo, nhi, base / mult)
        if chunk:
            alts.append(chunk + r"\s*" + re.escape(unit))
    if not alts:
        return None
    return r"^\s*(?:" + "|".join(alts) + ")"


def _component_value_type(comp: Component | None) -> tuple[float, str, str] | None:
    """(magnitude, unit_key, family) —— 优先用 IR 归一化值，兜底解析原始文本。"""
    if comp is None:
        return None
    val = comp.value
    if isinstance(val, ResistanceValue):
        key = _unit_key(val.unit)
        if key is None:
            return None
        return val.magnitude, key, "res"
    if isinstance(val, CapacitanceValue):
        key = _unit_key(val.unit)
        if key is None:
            return None
        return val.magnitude, key, "cap"
    if isinstance(val, InductanceValue):
        key = _unit_key(val.unit)
        if key is None:
            return None
        return val.magnitude, key, "ind"

    raw = str(getattr(comp, "raw_value", "") or "").strip()
    if not raw or raw.upper().startswith("NC"):
        return None
    m = _RAW_RES_RE.match(raw)
    if m:
        num = float(m.group(1))
        suffix = m.group(2).lower()
        mult = {"k": 1e3, "m": 1e6}.get(suffix, 1.0)
        return num * mult, {"k": "kω", "m": "mω"}.get(suffix, "ω"), "res"
    m = _RAW_CAP_RE.match(raw)
    if m:
        num = float(m.group(1))
        key = _unit_key(m.group(2))
        if key is None:
            return None
        return num, key, "cap"
    m = _RAW_IND_RE.match(raw)
    if m:
        num = float(m.group(1))
        key = _unit_key(m.group(2))
        if key is None:
            return None
        return num, key, "ind"
    return None


def value_type_pattern(comp: Component | None) -> str | None:
    """成员的值类型正则；参考板实测值为空/无法解析时返回 ``None``（成员不进模板）。"""
    got = _component_value_type(comp)
    if got is None:
        return None
    magnitude, unit_key, family = got
    if magnitude == 0:
        # 0Ω 跳线：值类型是"零欧"，不是"接近零"。
        if family == "res":
            return _ZERO_OHM_PATTERN
        return None
    return band_value_pattern(magnitude, unit_key, family)


# ---------------------------------------------------------------------------
# 板 / IR / 回放
# ---------------------------------------------------------------------------


def resolve_board_file(
    board: str | Path,
    *,
    fixtures: Path | None = None,
) -> Path:
    """解析板文件：**任意 .NET 路径**都可用，fixtures 下的文件名继续可用。"""
    raw = str(board)
    candidate = Path(raw)
    if candidate.exists() and candidate.is_file():
        return candidate.resolve()
    root = fixtures or FIXTURES_DIR
    matches = [p for p in root.rglob("*.NET") if p.is_file() and p.name == raw]
    if not matches:
        raise AutoDraftError(f"board not found: {raw}")
    if len(matches) > 1:
        protel = [p for p in matches if "protel" in p.as_posix()]
        return (protel[0] if protel else matches[0]).resolve()
    return matches[0].resolve()


def parse_board_ir(path: Path, *, db_path: Path | None = None) -> CircuitIR:
    """解析 + 富化板子（与 CLI 同一条链路）。"""
    from circuit_graph.cli.app import _parse_and_enrich, build_pin_category_provider

    return _parse_and_enrich(
        path,
        pin_category_provider=build_pin_category_provider(db_path),
    )


def _format_value(comp: Component | None) -> str:
    if comp is None:
        return ""
    from circuit_graph.generators.cg_graph import _format_component_value

    return _format_component_value(comp) or comp.raw_value or ""


def conn_section(ir: CircuitIR, designator: str) -> dict[str, Any]:
    """元件的引脚 → 网 → 同网元件（自动草稿用到的最小板视图）。"""
    from circuit_graph.cli.query import _CONN_MEMBER_CAP, _canonical, _comp_summary

    pin_to_net = {c.pin_id: c.net_id for c in ir.connections}
    net_members: dict[str, set[str]] = {}
    for c in ir.connections:
        comp_id = c.pin_id.rsplit(".", 1)[0]
        net_members.setdefault(c.net_id, set()).add(comp_id)
    comp_map = {c.id: c for c in ir.components}
    net_map = {n.id: n for n in ir.nets}

    comp = comp_map.get(_canonical(ir, designator))
    if comp is None:
        raise AutoDraftError(f"component not found: {designator}")

    pin_rows = []
    for pin in comp.pins:
        net_id = pin_to_net.get(pin.id)
        net_name = net_map[net_id].name if net_id in net_map else None
        members = []
        if net_id is not None:
            for cid in sorted(net_members.get(net_id, ())):
                member = comp_map.get(cid)
                if member is None:
                    continue
                members.append(
                    {
                        "id": cid,
                        "value": _format_value(member),
                        "pins_on_net": sorted(
                            p.number
                            for p in member.pins
                            if pin_to_net.get(p.id) == net_id
                        ),
                    }
                )
        truncated = max(0, len(members) - _CONN_MEMBER_CAP)
        if truncated:
            members = members[:_CONN_MEMBER_CAP]
        pin_rows.append(
            {
                "pin": pin.number,
                "name": pin.name,
                "net": net_name,
                "members": members,
                "truncated": truncated,
            }
        )
    return {"component": _comp_summary(comp), "pins": pin_rows}


def recognize_board(
    board_path: Path,
    *,
    primitives_dir: Path | None,
    db_path: Path | None = None,
    timeout_seconds: float | None = None,
) -> CircuitIR:
    """用指定模板库跑一遍识别（闸门 2 的回归对比用）。"""
    ir, _warnings = recognize_board_ex(
        board_path,
        primitives_dir=primitives_dir,
        db_path=db_path,
        timeout_seconds=timeout_seconds,
    )
    return ir


def recognize_board_ex(
    board_path: Path,
    *,
    primitives_dir: Path | None,
    db_path: Path | None = None,
    timeout_seconds: float | None = None,
) -> tuple[CircuitIR, list[str]]:
    """同 :func:`recognize_board`，但把识别器的 warnings 一起带回来。

    ``TopologyRecognizer`` 在单模板匹配超时时写 ``TOPOLOGY_MATCH_ABORTED``
    警告并跳过该模板——自动草稿靠它判断"这个模板太慢，不该进库"。
    """
    from circuit_graph.cli.app import build_pin_category_provider
    from circuit_graph.enrichers import run_enrichers
    from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher
    from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
    from circuit_graph.enrichers.net_classifier import NetClassifier
    from circuit_graph.enrichers.pin_resolver import PinNameResolver
    from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
    from circuit_graph.enrichers.value_normalizer import ValueNormalizer
    from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

    ir = ProtelNetParser().parse(ParserInput(file_path=str(board_path)))
    recognizer = TopologyRecognizer(
        primitives_dir=primitives_dir,
        pin_category_provider=build_pin_category_provider(db_path),
        timeout_seconds=timeout_seconds,
    )
    enrichers = [
        MultiUnitSplitter(),
        ValueNormalizer(),
        NetClassifier(),
        PinNameResolver(),
        EdgeSemanticEnricher(),
        recognizer,
    ]
    return run_enrichers(ir, enrichers).ir, list(recognizer.warnings)


def match_primitive_on_board(
    doc: dict[str, Any],
    board: str | Path,
    *,
    db_path: Path | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    """把**待写入的模板文档**回放到板子上（只带这一个模板的临时库）。"""
    tid = doc.get("id")
    if not isinstance(tid, str) or not tid:
        raise AutoDraftError("draft missing id")
    path = resolve_board_file(board)
    with tempfile.TemporaryDirectory(prefix="cg-template-") as tmp:
        tdir = Path(tmp)
        (tdir / f"{tid}.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8"
        )
        ir, warnings = recognize_board_ex(
            path,
            primitives_dir=tdir,
            db_path=db_path,
            timeout_seconds=timeout_seconds,
        )

    blocks_out = [
        {"block_id": b.id, "template_id": tid, "components": sorted(b.component_ids)}
        for b in ir.blocks
        if b.id.startswith(f"{tid}-")
    ]
    members = {c for b in blocks_out for c in b["components"]}
    known = {c.id for c in ir.components}
    return {
        "board": path.name,
        "netlist": path.as_posix(),
        "template_id": tid,
        "blocks": blocks_out,
        "matched_components": sorted(members & known),
        "warnings": warnings,
        "aborted": any("MATCH_ABORTED" in w for w in warnings),
    }


# ---------------------------------------------------------------------------
# 自动草稿
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    lowered = str(text or "").strip().lower()
    out = re.sub(r"[^a-z0-9]+", "-", lowered)
    return out.strip("-") or "template"


def _auto_slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")


def _auto_unique_id(base: str, used: set[str]) -> str:
    if base not in used:
        used.add(base)
        return base
    idx = 2
    while f"{base}_{idx}" in used:
        idx += 1
    name = f"{base}_{idx}"
    used.add(name)
    return name


def _auto_net_signal_id(net_name: str, pin_no: str) -> str:
    if _BOARD_NET_RE.match(net_name):
        return f"pin_{pin_no}"
    return "net_" + re.sub(r"[^a-z0-9]+", "_", net_name.strip().lower()).strip("_")


def _net_pattern(net_name: str) -> str:
    return _GND_NET_PATTERN if net_name.upper() in _GND_LIKE_NETS else ".*"


def _pin_names(
    chip_comp: Component,
    conn_rows: list[dict[str, Any]],
    part_number: str,
    db: Any,
) -> tuple[dict[str, str], str]:
    """引脚名解析链：KB → PDF(Camelot) → 网表回退。"""
    netlist_names = {
        str(row.get("pin")): str(row.get("name") or "") for row in conn_rows
    }
    if part_number and db is not None:
        try:
            pinout = db.get_pinout(part_number)
        except Exception:  # pragma: no cover - KB 不得拖垮草稿
            pinout = None
        if pinout:
            merged = {
                pin: str(pinout.get(pin) or netlist_names.get(pin) or "")
                for pin in netlist_names
            }
            if any(merged.values()):
                return merged, "kb"
    if part_number:
        try:
            from circuit_graph.knowledge.pdf_pin_extract import extract_from_datasheet

            pinout = extract_from_datasheet(part_number)
        except Exception:  # pragma: no cover - PDF 布局失败是常态
            pinout = None
        if pinout:
            merged = {
                pin: str(pinout.get(pin) or netlist_names.get(pin) or "")
                for pin in netlist_names
            }
            if any(merged.values()):
                return merged, "pdf"
    return netlist_names, "netlist"


def auto_draft(
    board: str | Path,
    chip: str,
    *,
    db: Any = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """板 + 芯片位号 → 自动模板草稿（不落盘）。

    返回 ``draft`` / ``state`` / ``conflicts`` / ``replay`` 等，与旧的
    ``scripts/template_web_lib.auto_draft`` 同构（新增 ``reference`` 等字段）。
    """
    chip = str(chip).strip()
    if not chip or "/" in chip or "\\" in chip or ".." in chip:
        raise AutoDraftError(f"invalid chip designator: {chip}")

    path = resolve_board_file(board)
    ir = parse_board_ir(path, db_path=db_path)

    conn = conn_section(ir, chip)
    chip_id = str(conn["component"]["id"])
    comp_map = {c.id: c for c in ir.components}
    chip_comp = comp_map[chip_id]
    part_number = chip_comp.raw_value or _format_value(chip_comp) or ""

    if db is None:
        try:
            from circuit_graph.knowledge.db import ComponentDatabase

            db = ComponentDatabase.default()
        except Exception:  # pragma: no cover - KB 可选
            db = None

    pin_names, pin_source = _pin_names(chip_comp, conn["pins"], part_number, db)

    # ---- 网成员表 -------------------------------------------------------
    net_name_of = {n.id: n.name for n in ir.nets}
    members_by_net: dict[str, set[str]] = {}
    #: 网名 → 该网的语义角色（power/gnd/analog/digital/clock），来自 EdgeSemanticEnricher。
    #: 只取 power / gnd 两种**强语义**角色写进模板的 expected_role —— 它们是
    #: 与板无关的电气语义（LDO 输出网在别的板上也还是电源网），却能把 VF2 的
    #: 候选网从"全部 100+ 个"压到"电源网/地网几个"，搜索空间小一个量级。
    net_role: dict[str, str] = {}
    for c in ir.connections:
        if c.net_id is None:
            continue
        sem = (c.metadata or {}).get("edge_semantic") or {}
        role = str(sem.get("role") or "")
        if role in ("power", "gnd"):
            net_role.setdefault(str(net_name_of.get(c.net_id, c.net_id)), role)
    for c in ir.connections:
        if c.net_id is None:
            continue
        comp_id = c.pin_id.rsplit(".", 1)[0]
        name = net_name_of.get(c.net_id, c.net_id)
        members_by_net.setdefault(str(name), set()).add(comp_id)

    # ---- 芯片引脚 → 信号 -------------------------------------------------
    chip_pin_rows: list[tuple[str, str, str]] = []
    for row in conn["pins"]:
        net = row.get("net")
        if not net:
            continue
        pin_no = str(row.get("pin"))
        name = pin_names.get(pin_no) or row.get("name") or ""
        chip_pin_rows.append((pin_no, str(name or ""), str(net)))

    signals: list[dict[str, Any]] = []
    signal_ids: set[str] = set()
    signal_by_net: dict[str, str] = {}
    signal_by_base: dict[str, str] = {}
    gnd_signal_id: str | None = None

    def _make_signal(sid: str, net: str, *, required: bool) -> dict[str, Any]:
        """一条网信号：网模式 + 强语义角色（power/gnd）。"""
        sig: dict[str, Any] = {
            "id": sid,
            "type": "net",
            "name_pattern": _net_pattern(net),
            "required": required,
        }
        role = net_role.get(net)
        if role:
            sig["expected_role"] = role
        return sig

    for pin_no, pin_name, net in chip_pin_rows:
        if net in signal_by_net:
            continue
        base = _auto_slug(pin_name)
        if not base:
            base = _auto_net_signal_id(net, pin_no)
        pattern = _net_pattern(net)
        existing = signal_by_base.get(base)
        if existing is not None:
            if pattern == _GND_NET_PATTERN:
                sid = existing
            else:
                sid = _auto_unique_id(base, signal_ids)
                signal_by_base[base] = sid
                signals.append(_make_signal(sid, net, required=True))
                signal_by_net[net] = sid
                continue
        else:
            sid = _auto_unique_id(base, signal_ids)
            signal_by_base[base] = sid
        signal_by_net[net] = sid
        signals.append(_make_signal(sid, net, required=True))
        if pattern == _GND_NET_PATTERN and gnd_signal_id is None:
            gnd_signal_id = sid

    chip_signal_ids = {signal_by_net[net] for _p, _n, net in chip_pin_rows}

    # ---- 成员发现 --------------------------------------------------------
    member_source_net: dict[str, str] = {}
    for _pin_no, _pin_name, net in chip_pin_rows:
        candidates = members_by_net.get(net, set()) - {chip_id}
        if len(candidates) > AUTO_MEMBER_CAP:
            continue
        for des in sorted(candidates):
            member_source_net.setdefault(des, net)

    new_net_counter = 0
    member_pins: dict[str, list[dict[str, Any]]] = {}
    member_net_of_signal: dict[str, str] = {}
    skipped_members: list[str] = []

    for des in sorted(member_source_net):
        try:
            sec = conn_section(ir, des)
        except AutoDraftError:
            skipped_members.append(des)
            continue
        pins: list[dict[str, Any]] = []
        complete = True
        for row in sec["pins"]:
            net = row.get("net")
            if not net:
                complete = False
                break
            net = str(net)
            if net in signal_by_net:
                sid = signal_by_net[net]
            elif _net_pattern(net) == _GND_NET_PATTERN:
                if gnd_signal_id is None:
                    gnd_signal_id = _auto_unique_id("gnd", signal_ids)
                    signal_by_net[net] = gnd_signal_id
                    signals.append(_make_signal(gnd_signal_id, net, required=False))
                sid = gnd_signal_id
            else:
                new_net_counter += 1
                sid = _auto_unique_id(f"net_{new_net_counter}", signal_ids)
                signal_by_net[net] = sid
                signals.append(_make_signal(sid, net, required=False))
                member_net_of_signal[sid] = net
            pins.append({"number": row["pin"], "net": sid})
        if not complete or not pins:
            skipped_members.append(des)
            continue
        member_pins[des] = pins

    # ---- 组件 ------------------------------------------------------------
    components: list[dict[str, Any]] = []

    chip_pin_count = len(chip_pin_rows)
    chip_entry: dict[str, Any] = {
        "id": "chip",
        "type": TYPE_BY_DEVICE_KIND.get(
            str(conn["component"].get("type", "unknown")), "Unknown"
        ),
        "required": True,
        "pins": [
            {"number": pin_no, "net": signal_by_net[net]}
            for pin_no, _pin_name, net in chip_pin_rows
        ],
    }
    if part_number and re.fullmatch(r"[A-Za-z0-9._+-]+", part_number):
        chip_entry["value_pattern"] = re.escape(part_number)
        chip_entry["strict_value"] = True
    components.append(chip_entry)

    # 成员按"可选组"聚合：同组共进退（像一颗功率级搭接件）。
    groups: dict[str, list[str]] = {}
    for des in sorted(member_pins):
        primary = signal_by_net.get(member_source_net[des], "net")
        groups.setdefault(primary, []).append(des)

    # 值类型正则：实测值为空（NC）/无法解析 → 成员不进模板。
    valued: dict[str, str] = {}
    no_value: list[str] = []
    for des in sorted(member_pins):
        pattern = value_type_pattern(comp_map.get(des))
        if pattern is None:
            no_value.append(des)
            continue
        valued[des] = pattern

    ranked_groups = sorted(groups.items(), key=lambda kv: (len(kv[1]), kv[0]))
    member_ids: set[str] = set()
    used_groups: set[str] = set()
    slots: list[dict[str, Any]] = []
    dropped_members: list[str] = []

    for primary, des_list in ranked_groups:
        usable = [d for d in des_list if d in valued]
        if not usable:
            dropped_members.extend(des_list)
            continue
        if (
            len(member_ids) + len(usable) > AUTO_MAX_MEMBERS
            or len(used_groups) >= AUTO_MAX_GROUPS
        ):
            dropped_members.extend(des_list)
            continue
        used_groups.add(primary)
        for des in usable:
            member_comp = comp_map.get(des)
            kind = (
                member_comp.device_kind.value.lower()
                if member_comp is not None
                else "unknown"
            )
            suffix = _AUTO_SUFFIX_BY_KIND.get(kind, "part")
            mid = _auto_unique_id(f"{primary}_{suffix}", member_ids)
            comp_type = TYPE_BY_DEVICE_KIND.get(kind, "Unknown")
            entry: dict[str, Any] = {
                "id": mid,
                "type": comp_type,
                "required": False,
                "optional_group": primary,
                "pins": member_pins[des],
                "value_pattern": valued[des],
                "strict_value": True,
            }
            components.append(entry)
            slots.append(
                {
                    "designator": des,
                    "member_id": mid,
                    "type": comp_type,
                    "optional_group": primary,
                    "title": f"{primary} 网上的 {kind}",
                }
            )

    # 只保留仍被引用的信号（成员裁剪后会有孤儿信号）。
    used_signals = {pin["net"] for c in components for pin in c["pins"]}
    signals = [s for s in signals if s["id"] in used_signals]

    # 成员独占网（参考板上不落任何 IC 脚）→ expected_ic_pin=forbidden，缩小候选网。
    ic_nets = {
        net
        for net, des_set in members_by_net.items()
        if any(
            comp_map[d].device_kind.value.lower() == "ic"
            for d in des_set
            if d in comp_map
        )
    }
    for sig in signals:
        if sig["id"] in chip_signal_ids:
            continue
        ref_net = member_net_of_signal.get(sig["id"])
        if ref_net is not None and ref_net not in ic_nets:
            sig["expected_ic_pin"] = "forbidden"

    # ---- 文档 ------------------------------------------------------------
    slug = _slugify(part_number) if part_number and _slugify(part_number) != "template" else "auto"
    draft_id = f"auto-{slug}"
    pin_names_text = ", ".join(
        f"{pin_no}={pin_name}" for pin_no, pin_name, _net in chip_pin_rows if pin_name
    )
    draft: dict[str, Any] = {
        # 自动模板落在 primitives/auto/，schema 在上一层目录。
        "$schema": "../schema.json",
        "id": draft_id,
        "name": f"{part_number or chip} 典型应用（自动）",
        "description": (
            f"自动模板：参考板 {path.name} 的 {chip}（{part_number or '型号未知'}）"
            f"及其同网无源件；引脚 {pin_names_text or '（引脚名未解析）'}；"
            "成员值为类型区间而非实测值，板网名不入库。"
        ),
        "version": "0.1.0",
        "source": "auto",
        "reference": {"board": path.name, "chip": chip},
        "strict_pin_matching": True,
        "signals": signals,
        "components": components,
        "constraints": {"min_components": 1, "exact_component_count": False},
        "scoring": deepcopy(DEFAULT_SCORING),
    }

    # ---- 回放 + 冲突 ------------------------------------------------------
    conflicts: list[str] = []
    warnings: list[str] = []
    replay: dict[str, Any] = {}
    replay_seconds = 0.0
    try:
        started = time.perf_counter()
        replay = match_primitive_on_board(
            draft,
            path,
            db_path=db_path,
            timeout_seconds=AUTO_MATCH_TIMEOUT_SECONDS,
        )
        replay_seconds = time.perf_counter() - started
    except Exception as exc:  # pragma: no cover - 回放失败按 needs_review 处理
        conflicts.append(f"replay_error:{exc}")
        replay = {"matched_components": [], "blocks": []}

    matched = list(replay.get("matched_components") or [])
    blocks = list(replay.get("blocks") or [])
    if replay.get("aborted"):
        # 单模板匹配超时：模板进了库就每块板都要陪它跑一遍 → 宁可现在不写。
        conflicts.append("replay_timeout")
    elif not blocks:
        conflicts.append("no_match")
    if len(matched) > AUTO_FLOOD_CAP:
        conflicts.append(f"flood:{len(matched)}")
    if dropped_members:
        # 成员超上限是**设计内的剪枝**（去掉电源/去耦这类外围件），不是缺陷，
        # 所以只记 warning，不阻断写入；阻断项见 conflicts。
        warnings.append(f"member_overflow:{len(dropped_members)}")
    if chip_pin_count > AUTO_MAX_CHIP_PINS:
        conflicts.append(f"too_many_chip_pins:{chip_pin_count}")
    warnings.append(f"replay_seconds:{replay_seconds:.1f}s")

    expected = {chip_id} | set(member_pins)
    ground_devices = {
        des
        for net, des_set in members_by_net.items()
        if _net_pattern(net) == _GND_NET_PATTERN
        for des in des_set
    }
    for des in matched:
        if des in expected:
            continue
        if des in ground_devices:
            continue
        conflicts.append(f"unexpected:{des}")

    state = "ready" if (blocks and matched and not conflicts) else "needs_review"

    checklist = [
        "闸门 0–3 全部通过才会写入 primitives/auto/",
        "人工模板优先：auto 块不抢已被手工模板认领的元件",
        f"手跑 pytest：{REPO_ROOT.as_posix()}/.venv/Scripts/python.exe -m pytest -q",
    ]

    return {
        "draft": draft,
        "checklist": checklist,
        "board": path.name,
        "board_path": path.as_posix(),
        "chip": chip,
        "chip_id": chip_id,
        "part_number": part_number,
        "slots": slots,
        "replay": replay,
        "conflicts": conflicts,
        "warnings": warnings,
        "state": state,
        "pin_source": pin_source,
        "skipped_members": skipped_members,
        "dropped_members": dropped_members,
        "no_value_members": no_value,
        "member_count": len(slots),
    }


# ---------------------------------------------------------------------------
# 机器闸门
# ---------------------------------------------------------------------------


def _template_of_block(block_id: str, template_ids: list[str]) -> str | None:
    from circuit_graph.primitives import template_of_block

    return template_of_block(block_id, template_ids)


def block_assignment(ir: CircuitIR, template_ids: list[str]) -> dict[str, str]:
    """元件 → 模板 id（沿用 ``cg primitives coverage`` 的"先到先得"归组）。"""
    known = {c.id for c in ir.components}
    out: dict[str, str] = {}
    for block in ir.blocks:
        tid = _template_of_block(block.id, template_ids)
        if tid is None:
            continue
        for cid in block.component_ids:
            if cid in known and cid not in out:
                out[cid] = tid
    return out


def _template_sources(primitives_dir: Path) -> dict[str, str]:
    """模板 id → source（"auto" 或 ""）。"""
    out: dict[str, str] = {}
    for path in sorted(primitives_dir.rglob("*.json")):
        if path.name == "schema.json":
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        pid = raw.get("id")
        if pid:
            out[pid] = str(raw.get("source") or "")
    return out


def _copy_library(primitives_dir: Path, tmp_root: Path) -> Path:
    """把模板库复制到临时目录（闸门 2 需要"加模板前后"两份库）。"""
    target = tmp_root / "primitives"
    target.mkdir(parents=True, exist_ok=True)
    for path in primitives_dir.rglob("*.json"):
        rel = path.relative_to(primitives_dir)
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    return target


def _answers_index(
    fixtures_dir: Path, board_stem: str | None = None
) -> dict[str, dict[str, str]]:
    """基准层：位号 → {block, confidence, source}（跨所有 ``*.answers.jsonl``）。

    ``board_stem`` 给定时只读**这块板**的 sidecar（``<stem>.answers.jsonl``）：
    不同板的位号会重名（两张板都有 C70），混在一起读会把别的板的答案当成
    这块板的结论。
    """
    index: dict[str, dict[str, str]] = {}
    for path in sorted(fixtures_dir.rglob("*.answers.jsonl")):
        if board_stem and not path.name.startswith(f"{board_stem}."):
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            des = str(rec.get("designator") or "")
            if not des:
                continue
            index[des] = {
                "block": str(rec.get("block") or ""),
                "confidence": str(rec.get("confidence") or ""),
                "source": str(rec.get("source") or ""),
            }
    return index


def _block_components(ir: CircuitIR, template_id: str) -> set[str]:
    """某个模板 id 在这块板上认领的元件集合。"""
    out: set[str] = set()
    for block in ir.blocks:
        if block.id == template_id or block.id.startswith(f"{template_id}-"):
            out.update(block.component_ids)
    return out


def _board_has_part(board_path: Path, part_number: str) -> bool:
    """板上是否存在该型号的元件（无型号约束的模板不能这样跳过）。"""
    if not part_number:
        return True
    needle = part_number.strip().lower()
    if not needle:
        return True
    try:
        text = board_path.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return True
    return needle in text


def evaluate_gates(
    draft_result: dict[str, Any],
    *,
    primitives_dir: Path | None = None,
    fixtures_dir: Path | None = None,
    db_path: Path | None = None,
    baseline: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """闸门 0–3。全部通过才允许写入；任一失败记录原因。

    * 闸门 0：参考板上该芯片已被**手工**模板命中 → 跳过（手工优先）。
    * 闸门 1：草稿 state 必须是 ``ready``。
    * 闸门 2：fixture 板"加模板前/后"对比，已被手工模板认领的元件
      不得改变块归属。
    * 闸门 3：基准层（*.answers.jsonl）里 auto 块覆盖的元件若已归到**另一个**
      具体模板 id → 拒。

    ``baseline`` 是跨芯片复用的"加模板前"归组缓存（板路径 → 元件 → 模板 id）。
    """
    primitives_dir = primitives_dir or PRIMITIVES_DIR
    fixtures_dir = fixtures_dir or FIXTURES_DIR
    baseline = baseline if baseline is not None else {}
    draft = draft_result["draft"]
    auto_id = draft["id"]
    board_path = Path(draft_result["board_path"])
    chip_id = draft_result.get("chip_id", draft_result["chip"])
    reasons: list[str] = []

    # ---- 闸门 1（最便宜，先判） -----------------------------------------
    if draft_result.get("state") != "ready":
        conflicts = draft_result.get("conflicts") or []
        reasons.append("gate1:" + (",".join(conflicts) if conflicts else "not_ready"))

    sources = _template_sources(primitives_dir)
    from circuit_graph.primitives import load_primitives

    template_ids = [t.id for t in load_primitives(primitives_dir)]

    def _before(board: Path) -> dict[str, str]:
        key = board.as_posix()
        if key not in baseline:
            ir = recognize_board(board, primitives_dir=primitives_dir, db_path=db_path)
            baseline[key] = block_assignment(ir, template_ids)
        return baseline[key]

    # ---- 闸门 0：手工模板已覆盖该芯片？ ---------------------------------
    base_ir = recognize_board(board_path, primitives_dir=primitives_dir, db_path=db_path)
    manual_cover = sorted(
        {
            tid
            for tid in template_ids
            if sources.get(tid, "") != "auto" and chip_id in _block_components(base_ir, tid)
        }
    )
    if manual_cover:
        reasons.append("gate0:manual:" + ",".join(manual_cover))

    # ---- 闸门 2 / 3 需要"加入 auto 模板后"的识别结果 ----------------------
    if not reasons:
        with tempfile.TemporaryDirectory(prefix="cg-gates-") as tmp:
            tmp_root = Path(tmp)
            lib = _copy_library(primitives_dir, tmp_root)
            auto_path = lib / AUTO_SUBDIR / f"{auto_id}.json"
            auto_path.parent.mkdir(parents=True, exist_ok=True)
            auto_path.write_text(
                json.dumps(draft, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            after_ids = template_ids + [auto_id]
            chip_strict = bool(
                next(
                    (
                        c.get("value_pattern")
                        for c in draft["components"]
                        if c.get("id") == "chip"
                    ),
                    None,
                )
            )
            for board in sorted(fixtures_dir.rglob("*.NET")):
                if not board.is_file():
                    continue
                # 芯片按型号严格匹配 → 板上没有该型号就不可能受影响（等价跳过）。
                if chip_strict and not _board_has_part(board, draft_result.get("part_number", "")):
                    continue
                before_map = _before(board)
                after_ir = recognize_board(board, primitives_dir=lib, db_path=db_path)
                after_map = block_assignment(after_ir, after_ids)
                for cid, old_tid in before_map.items():
                    if sources.get(old_tid, "") == "auto":
                        continue
                    new_tid = after_map.get(cid)
                    if new_tid != old_tid:
                        reasons.append(
                            f"gate2:{cid} block changed {old_tid} -> {new_tid}"
                        )
                        break
                if reasons:
                    break

            # 闸门 3：基准层冲突（auto 块实际覆盖的元件）
            if not reasons:
                after_ir = recognize_board(
                    board_path, primitives_dir=lib, db_path=db_path
                )
                auto_members = _block_components(after_ir, auto_id)
                # 只读参考板自己的基准层：别的板的同名位号（C70、R12…）答案不同。
                answers = _answers_index(fixtures_dir, board_path.stem)
                for des in sorted(auto_members):
                    rec = answers.get(des)
                    if not rec:
                        continue
                    ans_block = rec["block"].strip()
                    if not ans_block or ans_block == "(unmatched)":
                        continue
                    if ans_block == auto_id:
                        continue
                    reasons.append(f"gate3:{des} answer block {ans_block}")

    return {
        "passed": not reasons,
        "reasons": reasons,
        "details": {
            "auto_id": auto_id,
            "chip": draft_result.get("chip"),
            "board": draft_result.get("board"),
        },
    }


# ---------------------------------------------------------------------------
# 批量跑 + 写盘
# ---------------------------------------------------------------------------


def auto_template_path(primitives_dir: Path, template_id: str) -> Path:
    slug = template_id[len("auto-"):] if template_id.startswith("auto-") else template_id
    return primitives_dir / AUTO_SUBDIR / f"{slug}.json"


def write_auto_primitive(
    draft: dict[str, Any],
    *,
    primitives_dir: Path | None = None,
) -> Path:
    """把通过闸门的自动模板写进 ``primitives/auto/``（幂等：同 id 覆盖）。"""
    primitives_dir = primitives_dir or PRIMITIVES_DIR
    target = auto_template_path(primitives_dir, draft["id"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(draft, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def ic_designators(ir: CircuitIR) -> list[str]:
    """板上的 IC 类元件位号（``--all`` 的候选）。"""
    return sorted(
        c.id for c in ir.components if c.device_kind.value.lower() == "ic"
    )


def run_drafts(
    board: str | Path,
    *,
    chips: list[str] | None = None,
    all_chips: bool = False,
    write: bool = False,
    primitives_dir: Path | None = None,
    fixtures_dir: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """批量草稿 + 闸门 + （可选）写盘。返回报告数据。"""
    primitives_dir = primitives_dir or PRIMITIVES_DIR
    fixtures_dir = fixtures_dir or FIXTURES_DIR
    board_path = resolve_board_file(board, fixtures=fixtures_dir)
    ir = parse_board_ir(board_path, db_path=db_path)

    if all_chips or not chips:
        targets = ic_designators(ir)
    else:
        targets = list(chips)

    baseline: dict[str, dict[str, str]] = {}
    rows: list[dict[str, Any]] = []
    for chip in targets:
        started = time.perf_counter()
        row: dict[str, Any] = {"chip": chip, "state": "", "action": "", "reason": ""}
        try:
            draft_result = auto_draft(board_path, chip, db_path=db_path)
        except Exception as exc:
            row["state"] = "error"
            row["action"] = "skip"
            row["reason"] = f"error:{exc}"
            row["seconds"] = round(time.perf_counter() - started, 2)
            rows.append(row)
            continue
        row["part_number"] = draft_result.get("part_number", "")
        row["state"] = draft_result.get("state", "")
        row["conflicts"] = draft_result.get("conflicts", [])
        row["warnings"] = draft_result.get("warnings", [])
        row["matched"] = draft_result.get("replay", {}).get("matched_components", [])
        row["members"] = draft_result.get("member_count", 0)
        try:
            gates = evaluate_gates(
                draft_result,
                primitives_dir=primitives_dir,
                fixtures_dir=fixtures_dir,
                db_path=db_path,
                baseline=baseline,
            )
        except Exception as exc:
            gates = {"passed": False, "reasons": [f"gate_error:{exc}"], "details": {}}
        row["gates"] = gates
        if not gates["passed"]:
            row["action"] = "skip"
            row["reason"] = ";".join(gates["reasons"])
        elif write:
            path = write_auto_primitive(draft_result["draft"], primitives_dir=primitives_dir)
            row["action"] = "write"
            row["file"] = path.relative_to(primitives_dir).as_posix()
            row["reason"] = "gates passed"
        else:
            row["action"] = "would-write"
            row["reason"] = "gates passed (dry run)"
        row["seconds"] = round(time.perf_counter() - started, 2)
        rows.append(row)

    return {
        "board": board_path.name,
        "board_path": board_path.as_posix(),
        "rows": rows,
        "written": [r["file"] for r in rows if r.get("action") == "write"],
    }


def render_report(result: dict[str, Any]) -> str:
    """人类可读的批量草稿报告（Markdown）。"""
    lines: list[str] = []
    lines.append(f"# 自动模板草稿报告 — {result['board']}")
    lines.append("")
    lines.append(f"- 板：`{result['board_path']}`")
    lines.append("- 闸门：0 手工优先 / 1 ready / 2 回归 / 3 基准层冲突（任一失败不写）")
    lines.append("")
    lines.append("| 位号 | 型号 | state | 结论 | 耗时(s) |")
    lines.append("|---|---|---|---|---|")
    for row in result["rows"]:
        part = row.get("part_number") or "-"
        state = row.get("state") or "-"
        if row.get("action") == "write":
            verdict = f"✅ 写入 `{row.get('file')}`"
        elif row.get("action") == "would-write":
            verdict = "🟡 闸门通过（未开 --write）"
        else:
            verdict = f"⛔ {row.get('reason') or 'skip'}"
        lines.append(
            f"| {row.get('chip')} | {part} | {state} | {verdict} | {row.get('seconds')} |"
        )
    lines.append("")
    lines.append("## 明细")
    lines.append("")
    for row in result["rows"]:
        lines.append(f"### {row.get('chip')} ({row.get('part_number') or '-'})")
        lines.append("")
        lines.append(f"- state: `{row.get('state')}`")
        lines.append(f"- 结论: `{row.get('action')}` — {row.get('reason') or ''}")
        if row.get("conflicts"):
            lines.append(f"- 冲突码: `{', '.join(row['conflicts'])}`")
        if row.get("warnings"):
            lines.append(f"- 警告码: `{', '.join(row['warnings'])}`")
        if row.get("matched"):
            lines.append(f"- 回放命中: `{', '.join(row['matched'])}`")
        gates = row.get("gates") or {}
        if gates.get("reasons"):
            lines.append(f"- 闸门原因: `{'; '.join(gates['reasons'])}`")
        lines.append(f"- 耗时: `{row.get('seconds')}s`")
        lines.append("")
    return "\n".join(lines) + "\n"


__all__ = [
    "AUTO_MAX_GROUPS",
    "AUTO_MAX_MEMBERS",
    "AUTO_MAX_CHIP_PINS",
    "AUTO_MATCH_TIMEOUT_SECONDS",
    "AutoDraftError",
    "auto_draft",
    "auto_template_path",
    "band_value_pattern",
    "block_assignment",
    "conn_section",
    "evaluate_gates",
    "ic_designators",
    "match_primitive_on_board",
    "parse_board_ir",
    "recognize_board",
    "render_report",
    "resolve_board_file",
    "run_drafts",
    "value_type_pattern",
    "write_auto_primitive",
]
