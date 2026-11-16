"""元件作用推理引擎 —— 回答 "X 是干什么的"。

输入 CircuitIR + 一个 pinout provider（pin_number -> pin_name，通常来自 KB），
用一组有序拓扑规则对焦点元件做功能定性：

- opamp_feedback  运放反馈电阻 / 增益设定电阻（含增益计算）
- shunt           毫欧电流采样（低边 / 地桥 / 串联）
- divider         分压电阻（含分压比）
- pull_up/down    上拉 / 下拉电阻
- series_drive    IC 输出 → MOSFET 栅极驱动电阻
- rc_filter       RC 低通滤波（含 fc）
- decoupling      电源去耦电容
- coupling        交流耦合 / 隔直电容
- series_signal   通用串联电阻
- fallback        未命中：输出连接摘要

规则有序，首条命中即出结论。纯函数、不碰 KB / CLI，便于测试。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path

from circuit_graph import role_rules
from circuit_graph.enrichers.net_classifier import _classify_net
from circuit_graph.generators.cg_graph import _format_component_value
from circuit_graph.ir import (
    CapacitanceValue,
    CircuitIR,
    Component,
    NetType,
    ResistanceValue,
)
from circuit_graph.knowledge.chip_resolver import classify_pin_types

# RoleResult / 数值格式化在 role_types 里，与 role_rules 共用（避免循环 import）。
from circuit_graph.role_types import (
    RoleResult,
)
from circuit_graph.role_types import (
    fmt_freq as _fmt_freq,
)
from circuit_graph.role_types import (
    fmt_num as _fmt_num,
)

# part_number/pin_number -> pin_name；查不到返回 None（该 IC 引脚按 unknown 处理）
PinoutProvider = Callable[[Component], dict[str, str] | None]
# KB 器件事实（category/subcategory/…）；查不到返回空 dict
FactsProvider = Callable[[Component], dict[str, str]]

_RES_UNIT = {"Ω": 1.0, "kΩ": 1e3, "MΩ": 1e6}
_CAP_UNIT = {"pf": 1e-12, "nf": 1e-9, "µf": 1e-6, "uf": 1e-6}
# 这些类别的 IC 引脚被 R 拉住时才算上/下拉对象（unknown 单独放宽）
_INPUT_CATS = {"input", "gpio", "comm", "clock"}
# 上/下拉判定中，对端网络允许的最大成员数（大网基本是电源/总线，不是信号节点）
_PULL_MAX_DEGREE = 6
# role 规则里的"扩展地族"：_classify_net 刻意不把 CGND/VSS 归入 GND（b6ec2ce），
# 但毫欧电阻跨接它们与 GND 时就是回流路径，shunt 语义里需按地对待。
_GROUND_NAME_EXT_RE = re.compile(r"^(CGND|VSS)")
# 采样触碰认可的 IC 引脚类别；unknown 刻意排除（无 pinout 的 IC 引脚到处都是）
_SENSE_INPUT_CATS = {"input", "noninv_input", "inv_input"}


# ---------------------------------------------------------------------------
# 成员功能名（模板 component.title）与 fallback 标题
# ---------------------------------------------------------------------------

#: fallback / 成员标题共用的器件类别中文标签。
_KIND_LABELS = {
    "IC": "集成电路",
    "Inductor": "电感",
    "Mosfet": "MOSFET",
    "Diode": "二极管",
    "Connector": "接插件",
}


@lru_cache(maxsize=1)
def _template_map() -> dict[str, object]:
    """template id -> 模板对象（进程内只读缓存；目录变更走新进程）。"""
    from circuit_graph.primitives import load_primitives

    try:
        return {t.id: t for t in load_primitives()}
    except FileNotFoundError:
        return {}


def _member_head_title(template_id: str, role_label: str | None, focal: Component) -> str:
    """块成员的标题主名：模板给该成员的功能名，缺省用模板名。

    功能名支持 ``{value}`` / ``{kind}`` 占位（如接插件成员的 "{kind} {value}"
    还原 fallback 风格的"接插件 CON_2P_1.2MM"）。
    """
    tpl = _template_map().get(template_id)
    base: str | None = None
    if tpl is not None and role_label:
        base = next(
            (c.title for c in tpl.components if c.id == role_label and c.title),
            None,
        )
    if base is None:
        base = tpl.name if tpl is not None else template_id
    kind = _KIND_LABELS.get(focal.device_kind.value, focal.device_kind.value)
    part = focal.part_number or focal.raw_value or ""
    return base.replace("{kind}", kind).replace("{value}", part).strip()


def _normalize_part_text(text: str) -> str:
    """fallback 标题的值文本归一：电容/电感单位大小写与 "/" 两侧空格。

    基准层捕获的 fallback 标题是归一形（``Capacitor 10nF/50V``），而网表
    raw_value 常写作 ``10nf / 50V``；只归一**标题**，不动 evidence 里的原文。
    """
    out = re.sub(r"\s*/\s*", "/", text)
    out = re.sub(
        r"(\d+(?:\.\d+)?)\s*[µμu]([fh])\b",
        lambda m: m.group(1) + "u" + m.group(2).upper(),
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"(\d+(?:\.\d+)?)\s*([pnm])([fh])\b",
        lambda m: m.group(1) + m.group(2).lower() + m.group(3).upper(),
        out,
        flags=re.IGNORECASE,
    )
    return out


# ---------------------------------------------------------------------------
# IR 拓扑视图
# ---------------------------------------------------------------------------


class _Board:
    """焦点元件周围的拓扑查询视图。"""

    def __init__(
        self,
        ir: CircuitIR,
        focal: Component,
        provider: PinoutProvider,
        facts_provider: FactsProvider | None = None,
    ):
        self.ir = ir
        self.focal = focal
        self.provider = provider
        # KB 器件事实（类别/子类别…）的可选通道：comp -> dict | None。
        self.facts_provider = facts_provider
        self.pin2net = {c.pin_id: c.net_id for c in ir.connections}
        self.net_members: dict[str, set[str]] = {}
        for c in ir.connections:
            self.net_members.setdefault(c.net_id, set()).add(c.pin_id.rsplit(".", 1)[0])
        self.comp = {c.id: c for c in ir.components}
        self.net = {n.id: n for n in ir.nets}
        self._pinout_cache: dict[str, dict[str, str]] = {}
        self._facts_cache: dict[str, dict[str, str]] = {}

    def component_facts(self, comp: Component) -> dict[str, str]:
        """KB 里该型号的器件事实（类别/子类别…）；没有通道或查不到时为空。"""
        if self.facts_provider is None:
            return {}
        key = (comp.part_number or comp.raw_value or "").strip()
        if not key:
            return {}
        if key not in self._facts_cache:
            facts = self.facts_provider(comp)
            self._facts_cache[key] = dict(facts) if facts else {}
        return self._facts_cache[key]

    # -- 基本查询 -----------------------------------------------------------

    def net_of_pin(self, pin_id: str) -> str | None:
        return self.pin2net.get(pin_id)

    def net_name(self, net_id: str | None) -> str:
        if net_id is None or net_id not in self.net:
            return "?"
        return self.net[net_id].name

    def net_kind(self, net_id: str | None) -> NetType:
        """网络角色：优先用 enricher 结果，缺失时按名字补判。"""
        if net_id is None or net_id not in self.net:
            return NetType.UNKNOWN
        ntype = self.net[net_id].type
        if ntype is None or ntype is NetType.UNKNOWN:
            ntype, _ = _classify_net(self.net[net_id].name)
        return ntype

    def members(self, net_id: str | None) -> list[Component]:
        if net_id is None:
            return []
        return [
            self.comp[cid]
            for cid in sorted(self.net_members.get(net_id, ()))
            if cid in self.comp
        ]

    def pins_on_net(self, comp_id: str, net_id: str | None) -> list[str]:
        """元件 comp_id 落在 net_id 上的引脚号列表。"""
        if net_id is None:
            return []
        c = self.comp.get(comp_id)
        if c is None:
            return []
        return [p.number for p in c.pins if self.pin2net.get(p.id) == net_id]

    def value_text(self, comp: Component) -> str:
        return _format_component_value(comp) or comp.raw_value or "-"

    # -- IC 引脚语义 --------------------------------------------------------

    def pinout(self, ic: Component) -> dict[str, str]:
        if ic.id not in self._pinout_cache:
            po = self.provider(ic) or {}
            # IR 里已有的引脚名优先（resolver 结果可能比 KB 更贴合本设计）
            merged = dict(po)
            for p in ic.pins:
                if p.name and p.name not in ("?", ""):
                    merged[p.number] = p.name
            self._pinout_cache[ic.id] = merged
        return self._pinout_cache[ic.id]

    def pin_name(self, ic: Component, pin_number: str) -> str | None:
        return self.pinout(ic).get(pin_number)

    def pin_category(self, ic: Component, pin_number: str) -> str:
        """引脚语义类别：运放裸 +/- 特判，其余走 classify_pin_types。"""
        name = (self.pin_name(ic, pin_number) or "").strip()
        if name in ("+", "IN+", "+IN"):
            return "noninv_input"
        if name in ("-", "IN-", "-IN"):
            return "inv_input"
        if not name:
            return "unknown"
        return classify_pin_types({"0": name})["0"]

    def ics_on_net(self, net_id: str | None) -> list[tuple[Component, list[str]]]:
        """网络上的 IC 及其落点引脚号。"""
        out = []
        for m in self.members(net_id):
            if m.device_kind.value == "IC":
                out.append((m, self.pins_on_net(m.id, net_id)))
        return out

    def is_ic(self, comp: Component) -> bool:
        return comp.device_kind.value == "IC"


# ---------------------------------------------------------------------------
# 数值 helpers
# ---------------------------------------------------------------------------


def _resistance_ohms(comp: Component) -> float | None:
    v = comp.value
    if isinstance(v, ResistanceValue):
        return v.magnitude * _RES_UNIT.get(v.unit, 1.0)
    return None


def _capacitance_farads(comp: Component) -> float | None:
    v = comp.value
    if isinstance(v, CapacitanceValue):
        # 归一化器可能给小写单位（'nf'），按小写归一
        return v.magnitude * _CAP_UNIT.get(v.unit.lower(), 1e-6)
    return None


def _ic_label(ic: Component) -> str:
    part = ic.part_number or ic.raw_value or ""
    return f"{ic.id} {part}".strip()


def _net_label(board: _Board, net_id: str | None) -> str:
    """网络显示名：带 IC 引脚注释，如 "NetR97_1（U7.3 -）"。

    电源/地轨不注引脚（全网都是成员，注解只是噪声），重复 IC 只注前 2 个。
    """
    name = board.net_name(net_id)
    if board.net_kind(net_id) in (NetType.GND, NetType.POWER):
        return name
    notes = []
    for ic, pins in board.ics_on_net(net_id)[:2]:
        for pn in pins[:2]:
            pname = board.pin_name(ic, pn)
            notes.append(f"{ic.id}.{pn} {pname}" if pname else f"{ic.id}.{pn}")
    return f"{name}（{'; '.join(notes)}）" if notes else name


# ---------------------------------------------------------------------------
# 规则
# ---------------------------------------------------------------------------


def _find_opamp_ctx(board: _Board, net_ids: list[str]):
    """在两个网络里找运放上下文：同一 IC 的输出引脚在一个网、反相输入在另一个网。

    返回 (ic, out_net_id, inv_net_id, inv_pin)；找不到返回 None。
    """
    for net_id in net_ids:
        for ic, pins in board.ics_on_net(net_id):
            for pn in pins:
                if board.pin_category(ic, pn) == "output":
                    for other in net_ids:
                        if other == net_id:
                            continue
                        for ic2, pins2 in board.ics_on_net(other):
                            if ic2.id != ic.id:
                                continue
                            for pn2 in pins2:
                                if board.pin_category(ic, pn2) == "inv_input":
                                    return ic, net_id, other, pn2
    return None


def _resistors_on(board: _Board, net_id: str | None, exclude: str) -> list[Component]:
    return [
        m
        for m in board.members(net_id)
        if m.id != exclude and m.device_kind.value == "Resistor" and len(m.pins) == 2
    ]


def _resistor_to_gnd(board: _Board, r: Component) -> bool:
    for p in r.pins:
        if board.net_kind(board.net_of_pin(p.id)) is NetType.GND:
            return True
    return False


def _net_has_output_pin(board: _Board, net_id: str | None, ic_id: str) -> bool:
    for ic, pins in board.ics_on_net(net_id):
        if ic.id != ic_id:
            continue
        if any(board.pin_category(ic, pn) == "output" for pn in pins):
            return True
    return False


def _rule_opamp_feedback(board: _Board, pin_nets: list[str | None]) -> RoleResult | None:
    if board.focal.device_kind.value != "Resistor":
        return None
    nets = [n for n in pin_nets if n is not None]
    rf = _resistance_ohms(board.focal)

    # 情形 A：焦点跨 输出网 ↔ 反相输入网 → 反馈电阻 Rf
    ctx = _find_opamp_ctx(board, nets)
    if ctx is not None:
        ic, out_net, inv_net, inv_pin = ctx
        inv_name = board.pin_name(ic, inv_pin) or "-"
        rg = next(
            (
                r
                for r in _resistors_on(board, inv_net, board.focal.id)
                if _resistor_to_gnd(board, r)
            ),
            None,
        )
        rg_ohms = _resistance_ohms(rg) if rg else None
        ev = [
            f"{board.focal.id} {board.value_text(board.focal)}："
            f"{_net_label(board, out_net)} ↔ {_net_label(board, inv_net)}",
        ]
        data: dict[str, object] = {"ic": _ic_label(ic), "inverting_pin": inv_pin}
        if rf and rg_ohms:
            gain = 1 + rf / rg_ohms
            summary = (
                f"与 {rg.id}({board.value_text(rg)}) 设定同相增益 ≈ {_fmt_num(gain)} 倍"
                f"（1 + {board.value_text(board.focal)}/{board.value_text(rg)}），"
                f"输出见 {_net_label(board, out_net)}"
            )
            data["gain"] = round(gain, 3)
            data["rg"] = rg.id
        else:
            summary = f"把 {_ic_label(ic)} 输出反馈到反相输入，构成负反馈放大器"
        inv_others = _resistors_on(board, inv_net, board.focal.id)
        ev.append(
            f"反相端网络：{_net_label(board, inv_net)}，对地电阻："
            f"{', '.join(f'{r.id}({board.value_text(r)})' for r in inv_others) or '无'}"
        )
        return RoleResult(
            rule="opamp_feedback",
            title=f"反馈电阻（{_ic_label(ic)} 负反馈放大器）",
            summary=summary,
            evidence=ev,
            confidence="high",
            data=data,
        )

    # 情形 B：焦点跨 反相输入网 ↔ GND → 增益设定电阻 Rg
    # （输出引脚不在焦点的网络上，反馈 Rf 是同网络的另一个电阻）
    for inv_net in nets:
        for ic, pins in board.ics_on_net(inv_net):
            inv_pins = [pn for pn in pins if board.pin_category(ic, pn) == "inv_input"]
            if not inv_pins:
                continue
            if not any(n != inv_net and board.net_kind(n) is NetType.GND for n in nets):
                continue
            rf_r = next(
                (
                    r
                    for r in _resistors_on(board, inv_net, board.focal.id)
                    if any(
                        _net_has_output_pin(board, board.net_of_pin(p.id), ic.id)
                        for p in r.pins
                        if board.net_of_pin(p.id) != inv_net
                    )
                ),
                None,
            )
            if rf_r is None or rf is None:
                continue
            rf_ohms = _resistance_ohms(rf_r)
            if rf_ohms is None:
                continue
            inv_pin = inv_pins[0]
            inv_name = board.pin_name(ic, inv_pin) or "-"
            gain = 1 + rf_ohms / rf
            rf_out_net = board.net_of_pin(rf_r.pins[0].id)
            return RoleResult(
                rule="opamp_feedback_rg",
                title=f"增益设定电阻（{_ic_label(ic)} 反馈对中的 Rg）",
                summary=(
                    f"与 {rf_r.id}({board.value_text(rf_r)}) 设定同相增益 ≈ {_fmt_num(gain)} 倍"
                    f"（1 + {board.value_text(rf_r)}/{board.value_text(board.focal)}）"
                ),
                evidence=[
                    f"{board.focal.id} {board.value_text(board.focal)}："
                    f"{_net_label(board, inv_net)}（{inv_name}）↔ GND",
                    f"反馈电阻：{rf_r.id}({board.value_text(rf_r)})"
                    f" → {_net_label(board, rf_out_net)}",
                ],
                confidence="high",
                data={"ic": _ic_label(ic), "gain": round(gain, 3), "rf": rf_r.id},
            )
    return None


def _midpoint_ok(board: _Board, mid_net: str | None, focal_id: str) -> bool:
    """中点网络只连 IC 输入引脚 + 电阻（最多再带小电容），才像分压中点。"""
    ic_pins = [
        (ic, pn)
        for ic, pins in board.ics_on_net(mid_net)
        for pn in pins
        if board.pin_category(ic, pn) in _INPUT_CATS or board.pin_category(ic, pn) == "unknown"
    ]
    if not ic_pins:
        return False
    extras = [
        m
        for m in board.members(mid_net)
        if m.id != focal_id
        and m.device_kind.value not in ("Resistor", "Capacitor")
    ]
    return not extras


def _other_resistor_rail(
    board: _Board, mid_net: str | None, focal_id: str, rail_kind: NetType
) -> Component | None:
    """中点网络上另一端落在 rail_kind 轨的 2 脚电阻（分压对侧臂）。"""
    for r in _resistors_on(board, mid_net, focal_id):
        for p in r.pins:
            nid = board.net_of_pin(p.id)
            if nid != mid_net and board.net_kind(nid) is rail_kind:
                return r
    return None


def _rule_divider(board: _Board, pin_nets: list[str | None]) -> RoleResult | None:
    """分压电阻：焦点是一臂，另一臂在中点网络上通向另一侧电源轨。"""
    if board.focal.device_kind.value != "Resistor":
        return None
    kinds = [board.net_kind(n) for n in pin_nets]
    r_focal = _resistance_ohms(board.focal)

    # 上臂：焦点在电源轨 ↔ 中点，对臂从中点到 GND
    if NetType.POWER in kinds:
        i = kinds.index(NetType.POWER)
        mid = pin_nets[1 - i]
        if mid is None or NetType.GND in [board.net_kind(mid)]:
            return None
        bottom = _other_resistor_rail(board, mid, board.focal.id, NetType.GND)
        if bottom is None or not _midpoint_ok(board, mid, board.focal.id):
            return None
        r_bottom = _resistance_ohms(bottom)
        ratio = r_focal / (r_focal + r_bottom) if r_focal and r_bottom else None
        dest = ", ".join(
            f"{ic.id}.{pn}" for ic, pins in board.ics_on_net(mid) for pn in pins
        )
        summary = f"分压电阻（上臂），中点送 {dest}"
        if ratio is not None:
            summary += f"，中点电压 ≈ 电源 × {_fmt_num(ratio)}"
        return RoleResult(
            rule="divider",
            title=f"分压电阻（{board.net_name(mid)} 上臂）",
            summary=summary,
            evidence=[
                f"{board.focal.id} {board.value_text(board.focal)}："
                f"{board.net_name(pin_nets[i])} ↔ {_net_label(board, mid)}",
                f"下臂：{bottom.id}({board.value_text(bottom)}) → GND",
            ],
            confidence="medium",
            data={"ratio": round(ratio, 4) if ratio else None},
        )

    # 下臂：焦点在中点 ↔ GND，对臂从中点到电源轨
    if NetType.GND in kinds:
        i = kinds.index(NetType.GND)
        mid = pin_nets[1 - i]
        if mid is None:
            return None
        top = _other_resistor_rail(board, mid, board.focal.id, NetType.POWER)
        if top is None or not _midpoint_ok(board, mid, board.focal.id):
            return None
        r_top = _resistance_ohms(top)
        ratio = r_top / (r_top + r_focal) if r_focal and r_top else None
        dest = ", ".join(
            f"{ic.id}.{pn}" for ic, pins in board.ics_on_net(mid) for pn in pins
        )
        summary = f"分压电阻（下臂），中点送 {dest}"
        if ratio is not None:
            summary += f"，中点电压 ≈ 电源 × {_fmt_num(ratio)}"
        return RoleResult(
            rule="divider",
            title=f"分压电阻（{board.net_name(mid)} 下臂）",
            summary=summary,
            evidence=[
                f"{board.focal.id} {board.value_text(board.focal)}："
                f"{_net_label(board, mid)} ↔ GND",
                f"上臂：{top.id}({board.value_text(top)}) → 电源",
            ],
            confidence="medium",
            data={"ratio": round(ratio, 4) if ratio else None},
        )
    return None


def _is_ground_family(board: _Board, net_id: str | None) -> bool:
    """地族网络：NetType.GND，或 CGND/VSS 等按名字补充识别的地（不动 _classify_net）。"""
    if net_id is None:
        return False
    if board.net_kind(net_id) is NetType.GND:
        return True
    return bool(_GROUND_NAME_EXT_RE.match(board.net_name(net_id).upper()))


def _probeable_ground_ends(board: _Board, pin_nets: list[str | None]) -> list[str]:
    """双端地族时可探采样触碰的地端：扩展地族、但不是全局参考地（GND/PGND/…）。

    全局参考地网上到处是运放基准分压（如 R97 挂 U7 IN-），探它必误报。
    """
    return [
        n
        for n in pin_nets
        if n is not None
        and _is_ground_family(board, n)
        and _classify_net(board.net_name(n))[0] is not NetType.GND
    ]


def _shunt_sense_tap(
    board: _Board, net_id: str, focal_id: str
) -> tuple[Component, str, Component | None] | None:
    """在 net_id 上找采样触碰：直连 IC 输入脚，或经 1 个非焦点电阻到输入脚。

    返回 (ic, 引脚号, 经过的电阻或 None)；找不到返回 None。
    """
    for ic, pins in board.ics_on_net(net_id):
        for pn in pins:
            if board.pin_category(ic, pn) in _SENSE_INPUT_CATS:
                return ic, pn, None
    for r in _resistors_on(board, net_id, focal_id):
        others = {
            board.net_of_pin(p.id)
            for p in r.pins
            if board.net_of_pin(p.id) not in (None, net_id)
        }
        for other in sorted(others):
            if _is_ground_family(board, other):
                continue  # 桥到另一个地网不是采样触碰
            for ic, pins in board.ics_on_net(other):
                for pn in pins:
                    if board.pin_category(ic, pn) in _SENSE_INPUT_CATS:
                        return ic, pn, r
    return None


def _rule_shunt(board: _Board, pin_nets: list[str | None]) -> RoleResult | None:
    """毫欧级电阻 = 电流采样（不该被上/下拉规则抢走）。

    - 低边：一端地族、另一端非地 —— 电流经它回地，压降供采样。
    - 双端地族：地桥或低边回流采样，探到采样触碰才断言采样，否则按地桥报 medium。
    - 串联：两端都非地族 —— 串在电流路径里。
    """
    if board.focal.device_kind.value != "Resistor":
        return None
    r = _resistance_ohms(board.focal)
    if r is None or r > 0.1:
        return None
    if r == 0:
        raw = (board.focal.raw_value or "").strip()
        if not re.fullmatch(r"0+(?:\.0+)?m?[RΩ][-_][A-Za-z0-9]+", raw, re.IGNORECASE):
            return None
    ev_net = " ↔ ".join(_net_label(board, n) for n in pin_nets)
    ground_ends = [n for n in pin_nets if _is_ground_family(board, n)]
    data: dict[str, object] = {"ohms": r}

    if len(ground_ends) == 1:
        # 经典低边采样：结构已定，采样电路未必在网表里可见（sense line 常不画全）
        i = pin_nets.index(ground_ends[0])
        other = pin_nets[1 - i]
        if other is not None:
            ev_net = f"{_net_label(board, other)} ↔ GND"
        return RoleResult(
            rule="shunt",
            title="电流采样电阻（低边，对地）",
            summary=(
                f"毫欧级阻值({board.value_text(board.focal)})把回流电流转成压降供采样，"
                "损耗可忽略"
            ),
            evidence=[
                f"{board.focal.id} {board.value_text(board.focal)}：{ev_net}",
                "未发现采样触碰（如确为电流采样需人工确认）",
            ],
            confidence="high",
            data=data,
        )

    if len(ground_ends) == 2:
        for net_id in _probeable_ground_ends(board, pin_nets):
            tap = _shunt_sense_tap(board, net_id, board.focal.id)
            if tap is None:
                continue
            ic, pn, via = tap
            pname = board.pin_name(ic, pn)
            ptxt = f"({pname})" if pname else ""
            if via is not None:
                tap_line = (
                    f"采样触碰：{ic.id} pin{pn}{ptxt} ← {via.id} ← "
                    f"{board.net_name(net_id)}"
                )
                data["sense_tap"] = f"{ic.id}:{pn} via {via.id}"
            else:
                tap_line = (
                    f"采样触碰：{ic.id} pin{pn}{ptxt} 直连 {board.net_name(net_id)}"
                )
                data["sense_tap"] = f"{ic.id}:{pn} direct"
            return RoleResult(
                rule="shunt",
                title="电流采样电阻（低边回流采样）",
                summary=(
                    f"回流电流全部经 {board.focal.id}，"
                    f"压降被采样电路（{ic.id}）读取，损耗可忽略"
                ),
                evidence=[
                    f"{board.focal.id} {board.value_text(board.focal)}：{ev_net}",
                    tap_line,
                ],
                confidence="high",
                data=data,
            )
        bridge = " ↔ ".join(board.net_name(n) for n in pin_nets if n is not None)
        data["sense_tap"] = None
        return RoleResult(
            rule="shunt",
            title=f"毫欧地桥电阻（{bridge} 跨接）",
            summary=(
                f"毫欧级阻值({board.value_text(board.focal)})跨接两个地网络；"
                "未发现采样电路触碰，若确为电流采样需人工确认"
            ),
            evidence=[f"{board.focal.id} {board.value_text(board.focal)}：{ev_net}"],
            confidence="medium",
            data=data,
        )

    return RoleResult(
        rule="shunt",
        title="电流采样电阻（串联）",
        summary=(
            f"毫欧级阻值({board.value_text(board.focal)})串在电流路径里把电流转成压降，"
            "损耗可忽略"
        ),
        evidence=[f"{board.focal.id} {board.value_text(board.focal)}：{ev_net}"],
        confidence="high",
        data=data,
    )


def _rule_pull(board: _Board, pin_nets: list[str | None]) -> RoleResult | None:
    if board.focal.device_kind.value != "Resistor":
        return None
    for i, net_a in enumerate(pin_nets):
        if net_a is None:
            continue
        kind_a = board.net_kind(net_a)
        if kind_a not in (NetType.GND, NetType.POWER):
            continue
        net_b = pin_nets[1 - i]
        # 对端必须是信号节点（名字没被分类的未知小网也算），电源/地轨除外
        if board.net_kind(net_b) in (NetType.GND, NetType.POWER):
            continue
        members_b = board.members(net_b)
        if len(members_b) > _PULL_MAX_DEGREE:
            continue
        ic_pins = [
            (ic, pn, board.pin_category(ic, pn))
            for ic, pins in board.ics_on_net(net_b)
            for pn in pins
        ]
        # 对端必须有 IC 引脚（或至少有非无源器件），否则只是泄放
        cats = {c for _, _, c in ic_pins}
        if not ic_pins and not any(m.device_kind.value in ("Mosfet", "Diode") for m in members_b):
            continue
        if cats and not (cats & (_INPUT_CATS | {"unknown"})):
            continue
        direction = "上拉" if kind_a is NetType.POWER else "下拉"
        rail = board.net_name(net_a)
        dest_parts = []
        for ic, pn, _cat in ic_pins:
            pname = board.pin_name(ic, pn)
            dest_parts.append(
                f"{ic.id}.{pn}" + (f"({pname})" if pname else "")
            )
        dest = ", ".join(dest_parts) or "对端网络"
        return RoleResult(
            rule="pull_up" if kind_a is NetType.POWER else "pull_down",
            title=f"{direction}电阻（{board.net_name(net_b)} → {rail}）",
            summary=f"把 {board.net_name(net_b)} 稳定在 {rail}，给 {dest} 提供确定电平",
            evidence=[
                f"{board.focal.id} {board.value_text(board.focal)}："
                f"{_net_label(board, net_b)} ↔ {rail}"
            ],
            confidence="medium" if ic_pins else "low",
        )
    return None


def _rule_series_drive(board: _Board, pin_nets: list[str | None]) -> RoleResult | None:
    if board.focal.device_kind.value != "Resistor":
        return None
    for i, net_a in enumerate(pin_nets):
        net_b = pin_nets[1 - i]
        outs = [
            (ic, pn)
            for ic, pins in board.ics_on_net(net_a)
            for pn in pins
            if board.pin_category(ic, pn) == "output"
        ]
        if not outs:
            continue
        mosfets = [m for m in board.members(net_b) if m.device_kind.value == "Mosfet"]
        if not mosfets:
            continue
        ic_dest = ", ".join(f"{ic.id}.{pn}" for ic, pn in outs)
        mos_dest = ", ".join(f"{m.id}.{','.join(board.pins_on_net(m.id, net_b))}" for m in mosfets)
        return RoleResult(
            rule="series_drive",
            title=f"栅极驱动电阻（{ic_dest} → {mosfets[0].id}）",
            summary="串在驱动通路里限流、抑制栅极振荡",
            evidence=[
                f"{board.focal.id} {board.value_text(board.focal)}："
                f"{_net_label(board, net_a)} ↔ {board.net_name(net_b)}（{mos_dest}）"
            ],
            confidence="medium",
        )
    return None


def _rule_rc_filter(board: _Board, pin_nets: list[str | None]) -> RoleResult | None:
    """R 串在信号通路、下游网络有 C 到 GND → 低通滤波。"""
    if board.focal.device_kind.value != "Resistor":
        return None
    r_ohms = _resistance_ohms(board.focal)
    for i, net_a in enumerate(pin_nets):
        net_b = pin_nets[1 - i]
        # 滤波电容应挂在信号节点上；全局电源/地轨上的 C 是去耦，归 decoupling 规则
        if board.net_kind(net_b) in (NetType.GND, NetType.POWER):
            continue
        caps = []
        for m in board.members(net_b):
            if m.device_kind.value != "Capacitor" or len(m.pins) != 2:
                continue
            if any(board.net_kind(board.net_of_pin(p.id)) is NetType.GND for p in m.pins):
                caps.append(m)
        if not caps:
            continue
        c = caps[0]
        c_farads = _capacitance_farads(c)
        summary = f"与 {c.id}({board.value_text(c)}) 组成 RC 低通，滤除高频噪声"
        data: dict[str, object] = {"capacitor": c.id}
        if r_ohms and c_farads:
            fc = 1.0 / (2 * 3.141592653589793 * r_ohms * c_farads)
            summary += f"，fc ≈ {_fmt_freq(fc)}"
            data["fc_hz"] = round(fc, 2)
        return RoleResult(
            rule="rc_filter",
            title=f"RC 滤波电阻（{_net_label(board, net_b)}）",
            summary=summary,
            evidence=[
                f"{board.focal.id} {board.value_text(board.focal)}："
                f"{_net_label(board, net_a)} → {_net_label(board, net_b)}，"
                f"{c.id} 对地"
            ],
            confidence="high",
            data=data,
        )
    return None


def _rule_series_signal(board: _Board, pin_nets: list[str | None]) -> RoleResult | None:
    if board.focal.device_kind.value != "Resistor":
        return None
    ends = []
    for n in pin_nets:
        ics = board.ics_on_net(n)
        if ics:
            ic, pins = ics[0]
            pn = pins[0]
            pname = board.pin_name(ic, pn)
            ends.append(f"{ic.id}.{pn}" + (f"({pname})" if pname else ""))
        else:
            ends.append(board.net_name(n))
    return RoleResult(
        rule="series_signal",
        title="串联电阻（信号通路）",
        summary="串联在信号通路中：限流 / 阻抗匹配 / 便于调试割断，需结合前后级判断",
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}："
            f"{ends[0] if ends else '?'} → {ends[1] if len(ends) > 1 else '?'}"
        ],
        confidence="low",
    )


def _rule_decoupling(board: _Board, pin_nets: list[str | None]) -> RoleResult | None:
    if board.focal.device_kind.value != "Capacitor":
        return None
    kinds = [board.net_kind(n) for n in pin_nets]
    if NetType.GND not in kinds or NetType.POWER not in kinds:
        return None
    rail = board.net_name(pin_nets[kinds.index(NetType.POWER)])
    # 电源网上是否有 IC 电源引脚
    pnet = pin_nets[kinds.index(NetType.POWER)]
    powered = [
        f"{ic.id}.{pn}"
        for ic, pins in board.ics_on_net(pnet)
        for pn in pins
        if board.pin_category(ic, pn) == "power_in"
    ]
    summary = f"滤掉 {rail} 上的高频噪声，给本地电源退耦"
    if powered:
        summary += f"（供电：{', '.join(powered[:3])}）"
    gnd_net = pin_nets[kinds.index(NetType.GND)]
    return RoleResult(
        rule="decoupling",
        title=f"去耦电容（{rail} ↔ GND）",
        summary=summary,
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}："
            f"{rail} ↔ {board.net_name(gnd_net)}"
        ],
        confidence="high",
    )


def _rule_coupling_cap(board: _Board, pin_nets: list[str | None]) -> RoleResult | None:
    """电容跨在两个非电源网络之间 → 耦合/隔直；一端对 GND 的情形归滤波。"""
    if board.focal.device_kind.value != "Capacitor":
        return None
    kinds = [board.net_kind(n) for n in pin_nets]
    if NetType.GND in kinds or NetType.POWER in kinds:
        return None
    if any(k in (NetType.UNKNOWN,) for k in kinds):
        # 至少一端要有 IC/信号成员才像耦合
        pass
    ends = []
    has_ic = False
    for n in pin_nets:
        ics = board.ics_on_net(n)
        if ics:
            has_ic = True
            ic, pins = ics[0]
            ends.append(f"{ic.id}.{pins[0]}")
        else:
            ends.append(board.net_name(n))
    if not has_ic:
        return None
    return RoleResult(
        rule="coupling",
        title="交流耦合 / 隔直电容",
        summary="隔直流通交流，把前级信号送进后级而不传递直流偏置",
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}："
            f"{ends[0] if ends else '?'} → {ends[1] if len(ends) > 1 else '?'}"
        ],
        confidence="medium",
    )


def _rule_filter_cap(board: _Board, pin_nets: list[str | None]) -> RoleResult | None:
    """对地电容（非电源轨）→ 旁路/滤波/晶振负载。"""
    if board.focal.device_kind.value != "Capacitor":
        return None
    for i, net_a in enumerate(pin_nets):
        if board.net_kind(net_a) is not NetType.GND:
            continue
        net_b = pin_nets[1 - i]
        if net_b is None or board.net_kind(net_b) is NetType.POWER:
            continue
        cats = {
            board.pin_category(ic, pn)
            for ic, pins in board.ics_on_net(net_b)
            for pn in pins
        }
        if "clock" in cats:
            return RoleResult(
                rule="crystal_load",
                title=f"晶振负载电容（{_net_label(board, net_b)}）",
                summary="与对侧负载电容共同决定晶振频率，并提供起振条件",
                evidence=[
                    f"{board.focal.id} {board.value_text(board.focal)}："
                    f"{_net_label(board, net_b)} ↔ GND"
                ],
                confidence="medium",
            )
        return RoleResult(
            rule="filter_cap",
            title=f"滤波/旁路电容（{_net_label(board, net_b)}）",
            summary="对地滤除该节点上的高频分量",
            evidence=[
                f"{board.focal.id} {board.value_text(board.focal)}："
                f"{_net_label(board, net_b)} ↔ GND"
            ],
            confidence="medium",
        )
    return None


_RES_RULES = [
    _rule_opamp_feedback,
    _rule_shunt,
    _rule_divider,
    _rule_pull,
    _rule_series_drive,
    _rule_rc_filter,
    _rule_series_signal,
]
_CAP_RULES = [
    _rule_decoupling,
    _rule_coupling_cap,
    _rule_filter_cap,
]


def _merge_semantic(result: RoleResult, detailed: RoleResult) -> RoleResult:
    """把实例级细节并进模板结果，**不改模板给出的功能名**。

    历史实现直接 ``return detailed``：手写规则整体顶掉模板标题
    （"DC 电源去耦电容 · C_dec" → "去耦电容（DCIN ↔ GND）"）。后果有两个：

    1. 标题说的功能与 block 记录的模板分类对不上——同一个模板下标题各不相同，
       读者无法从标题判断这是哪一类电路；
    2. 基准层（``*.answers.jsonl``）记录的 function 就是模板的功能名，
       标题被改写后两边永远对不齐，对齐报告全是假分歧。

    现在：**标题恒为模板的功能定性**（实例细节不再改写标题），
    增益 / fc / 配对位号 / 具体网络这类实例证据进 summary 与 evidence，
    仍然完整出现在 ``cg role`` 的输出里。
    """
    detailed.rule = result.rule
    instance_title = detailed.title
    detailed.title = result.title
    if not detailed.summary:
        detailed.summary = result.summary
    evidence = list(result.evidence)
    # 手写规则的标题里带着实例信息（配对位号、IC 型号、具体网络），
    # 改写标题前先留一份，读者在 evidence 里仍能拿到同样的上下文。
    if instance_title and instance_title != result.title:
        evidence.insert(0, f"实例定性：{instance_title}")
    for item in detailed.evidence:
        if item not in evidence:
            evidence.append(item)
    detailed.evidence = evidence
    if result.data:
        merged = dict(result.data)
        merged.update(detailed.data or {})
        detailed.data = merged
    return detailed


def _semantic_details(
    board: _Board,
    pin_nets: list[str | None],
    template_id: str,
    result: RoleResult,
) -> RoleResult:
    """给模板命中补回数值与关键证据，不改变模板的匹配/选择结果与功能名。

    模板负责判定拓扑；这些已有规则只负责把同一拓扑翻译成工程师需要的
    增益、fc、配对位号、具体网络等说明。没有可靠证据时保留模板原答案。
    """
    if template_id == "opamp-noninv-feedback":
        detailed = _rule_opamp_feedback(board, pin_nets)
        if detailed is None:
            return result
        # rule 继续指向模板（不是旧手写规则 id），其余采用更具体的说明。
        return _merge_semantic(result, detailed)
    if template_id == "rc-low-pass":
        if board.focal.device_kind.value == "Resistor":
            detailed = _rule_rc_filter(board, pin_nets)
            if detailed is not None:
                detailed = _merge_semantic(result, detailed)
                output_net = next(
                    (
                        n for n in pin_nets
                        if board.net_kind(n) not in (NetType.GND, NetType.POWER)
                        and any(
                            board.net_kind(board.net_of_pin(p.id)) is NetType.GND
                            for c in board.members(n)
                            if c.device_kind.value == "Capacitor"
                            for p in c.pins
                        )
                    ),
                    None,
                )
                if output_net is not None:
                    detailed.evidence.insert(
                        0, f"滤波输出节点：{_net_label(board, output_net)}"
                    )
                return detailed
        else:
            for i, net_a in enumerate(pin_nets):
                if board.net_kind(net_a) is not NetType.GND:
                    continue
                other = pin_nets[1 - i]
                result.evidence.insert(0, f"电容接入节点：{_net_label(board, other)}")
                result.summary = "与串联电阻组成 RC 低通，旁路该节点的高频分量"
                break
    elif template_id == "dc-decoupling-cap":
        detailed = _rule_decoupling(board, pin_nets)
        if detailed is not None:
            return _merge_semantic(result, detailed)
    elif template_id in {"current-shunt-monitor", "shunt"}:
        # 模板命中电流采样只代表阻值/串联结构成立；双地桥接且没有
        # 采样抽头时必须诚实降级为“地桥”，不能沿用模板的采样断言。
        detailed = _rule_shunt(board, pin_nets)
        if detailed is not None:
            bridge_title = (
                detailed.title
                if "sense_tap" in detailed.data and detailed.data["sense_tap"] is None
                else None
            )
            merged = _merge_semantic(result, detailed)
            if bridge_title is not None:
                merged.title = bridge_title
            return merged
    elif template_id in {"pull-up-resistor", "pull-down-resistor"}:
        detailed = _rule_pull(board, pin_nets)
        if detailed is not None:
            return _merge_semantic(result, detailed)
    return result


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


class LiveMatchSession:
    """现场 primitives 匹配的**可复用**上下文（批量定性用）。

    VF2 模板搜索是**整板级**的：``matcher.find_matches(t)`` 的结果与焦点元件
    无关，同一个模板对所有焦点元件只需要跑一次。批量（``cg role --all``）
    时共享一个 session，把 "N 个焦点 × M 个模板" 次搜索压成 "M 次搜索"，
    这正是 207 个元件从 191 s 降到个位数秒的关键。

    单件路径（``live_session=None``）行为与改造前逐字节一致。
    """

    def __init__(self, ir: CircuitIR, kb_path: Path | None = None) -> None:
        self.ir = ir
        self.kb_path = kb_path
        self._templates: list | None = None
        self._matcher: object | None = None
        self._hits: dict[str, list[tuple]] = {}

    # -- 惰性资源 -----------------------------------------------------------

    @property
    def templates(self) -> list:
        if self._templates is None:
            from circuit_graph.primitives import load_primitives

            self._templates = load_primitives()
        return self._templates

    @property
    def matcher(self):
        if self._matcher is None:
            from circuit_graph.vf2_matcher import Vf2TopologyMatcher

            self._matcher = Vf2TopologyMatcher(
                self.ir,
                ignore_pin_numbers=True,
                pin_category_provider=self._pin_category_provider(),
            )
        return self._matcher

    def _pin_category_provider(self):
        from circuit_graph.cli.app import DEFAULT_KB_PATH
        from circuit_graph.knowledge.chip_resolver import classify_pin_types
        from circuit_graph.knowledge.db import ComponentDatabase

        kb_path = self.kb_path or DEFAULT_KB_PATH
        try:
            db = ComponentDatabase.open(kb_path)
        except Exception:  # noqa: BLE001
            db = None

        cat_cache: dict[str, dict[str, str]] = {}

        def provider(comp: Component) -> dict[str, str] | None:
            if db is None:
                return None
            part = comp.part_number or comp.raw_value
            if not part:
                return None
            if part in cat_cache:
                return cat_cache[part]
            rec = db.get_component(part)
            if rec is None:
                cat_cache[part] = {}
                return None
            cats = classify_pin_types(db.get_pinout(rec.part_number) or {})
            cat_cache[part] = cats
            return cats

        return provider

    # -- 模板命中（按模板缓存）-----------------------------------------------

    def hits_for(self, template) -> list[tuple]:
        """返回某模板在整板上的全部命中（已剔除 confidence=unknown）。

        超时（``aborted``）的结果**不缓存**：一次超时只影响当前这次搜索，
        不应该把整个批次的该模板判死。
        """
        key = template.id
        cached = self._hits.get(key)
        if cached is not None:
            return cached

        from circuit_graph.scoring import score_matches

        comp_map = {c.id: c for c in self.ir.components}
        net_map = {n.id: n for n in self.ir.nets}
        aborted = False
        try:
            result = self.matcher.find_matches(
                template, timeout_seconds=2.0, max_matches=200
            )
            aborted = result.aborted
        except Exception:  # noqa: BLE001
            hits: list[tuple] = []
        else:
            scored = score_matches(
                result.matches,
                template,
                comp_map,
                net_map,
                weights=template.scoring.weights,
                thresholds=template.scoring.thresholds,
            )
            hits = [(template, m, bd) for m, bd in scored if bd.confidence != "unknown"]
        if not aborted:
            self._hits[key] = hits
        return hits


def _match_focal_live(
    ir: CircuitIR,
    focal: Component,
    board: "_Board",
    session: LiveMatchSession | None = None,
) -> RoleResult | None:
    """现场跑 primitives 模板，查 focal 元件的命中。

    当 ir.blocks 被 flood 机制降级时使用。只返回含 focal 的命中，
    按 (specific 优先, component_ids 数升序, template.id) 排序选首个。

    ``session`` 为 None 时现场建一个一次性上下文（单件路径，行为不变）；
    批量路径传入共享 session，模板搜索只跑一次。
    """
    if session is None:
        session = LiveMatchSession(ir)

    generic_prefixes = (
        "rc-high-pass", "rc-low-pass", "lc-pi-filter",
        "voltage-divider", "inverting-amplifier", "opamp-unit",
        "differential-comparator", "sallen-key",
    )
    # 宽泛模板：仅靠"一端 GND 一端信号"就命中，最容易和具体功能块重叠
    loose_templates = {"pull-down-resistor", "pull-up-resistor", "series-gate-drive"}

    def _specificity(t) -> tuple:
        """模板具体度：值越小越具体。
        1) 带 expected_pin_category 的模板最具体（语义约束最强）
        2) 宽泛模板（pull-up/down 等）最后
        3) 其他按 component 数降序（元件多的模板更具体）
        """
        has_pin_cat = any(s.expected_pin_category for s in t.signals)
        is_loose = t.id in loose_templates
        return (0 if has_pin_cat else 1, 1 if is_loose else 0, -len(t.components))

    specific_hits: list[tuple] = []
    generic_hits: list[tuple] = []
    for t in session.templates:
        # 粗过滤：focal 必须是 template 里某个 component 的类型
        focal_kind = focal.device_kind.value
        if not any(
            focal_kind in (ct if isinstance(ct, tuple) else (ct,))
            for ct in [c.type for c in t.components]
        ):
            continue
        for hit_t, m, bd in session.hits_for(t):
            if focal.id not in m.component_mapping.values():
                continue
            bucket = (
                generic_hits
                if any(hit_t.id.startswith(p) for p in generic_prefixes)
                else specific_hits
            )
            bucket.append((hit_t, m, bd))

    specific_hits.sort(key=lambda h: (_specificity(h[0]), h[0].id))
    generic_hits.sort(key=lambda h: (_specificity(h[0]), h[0].id))
    hits = specific_hits or generic_hits
    if not hits:
        return None
    t, m, bd = hits[0]

    # 宽泛模板（rc-low-pass / voltage-divider …）如果命中的元件集合**严格包含**
    # 窄模板的命中集合，说明它解释了更多上下文，优先用它：
    #   - R102：窄模板只看到"一端到地的电阻"（pull-down），宽泛模板看到
    #     电阻 + 电容构成的 RC 滤波网络；
    #   - R65：窄模板只看到"一端到地的电阻"，宽泛模板看到上臂+下臂的分压网络。
    if specific_hits and generic_hits:
        specific_comps = set(specific_hits[0][1].component_mapping.values())
        covering = [
            h for h in generic_hits
            if specific_comps < set(h[1].component_mapping.values())
        ]
        if covering:
            t, m, bd = covering[0]

    # 找 focal 在模板里的角色
    role_label = next(
        (role for role, cid in m.component_mapping.items() if cid == focal.id),
        None,
    )
    # 标题主名与块路径一致：模板成员功能名优先，缺省用模板名。
    member_title = next(
        (c.title for c in t.components if c.id == role_label and c.title),
        None,
    ) if role_label else None
    if member_title:
        kind = _KIND_LABELS.get(focal.device_kind.value, focal.device_kind.value)
        part = focal.part_number or focal.raw_value or ""
        head = member_title.replace("{kind}", kind).replace("{value}", part).strip()
    else:
        head = t.name
    title = head if not role_label else f"{head} · {role_label}"
    pin_nets = [board.net_of_pin(p.id) for p in focal.pins]
    ev = [
        f"template: {t.id} (live match, confidence={bd.confidence}, score={bd.fused:.2f})",
        f"signal_mapping: {m.signal_mapping}",
        f"component_mapping: {m.component_mapping}",
    ]
    for p, n in zip(focal.pins, pin_nets, strict=False):
        ev.append(f"pin {p.number}({p.name or '-'}) -> {_net_label(board, n)}")
    return _semantic_details(
        board,
        pin_nets,
        t.id,
        RoleResult(
            rule=f"primitive-live:{t.id}",
            title=title,
            summary=t.description or f"作为 {t.name} 的一部分",
            evidence=ev,
            confidence=bd.confidence,
        ),
    )


def infer_role(
    ir: CircuitIR,
    designator: str,
    pinout_provider: PinoutProvider,
    live_session: LiveMatchSession | None = None,
    facts_provider: FactsProvider | None = None,
) -> RoleResult | None:
    """对 designator 做功能定性；找不到元件返回 None。

    ``live_session`` 供批量调用共享现场模板搜索结果；None 时行为与从前一致。
    ``facts_provider`` 可选：提供 KB 器件事实（类别/子类别），供需要"器件
    身份证据"的规则（如 SPI 外设 vs 串行存储器）使用。
    """
    if live_session is not None and live_session.ir is not ir:
        raise ValueError("live_session belongs to a different CircuitIR")
    ids = {c.id: c for c in ir.components}
    focal = ids.get(designator)
    if focal is None:
        lowered = designator.lower()
        focal = next((c for c in ir.components if c.id.lower() == lowered), None)
    if focal is None:
        return None

    board = _Board(ir, focal, pinout_provider, facts_provider=facts_provider)
    pin_nets = [board.net_of_pin(p.id) for p in focal.pins]

    # 0) 属性规则：先判定"这是什么器件"（晶振 / 保险丝 / 三极管 / 显示模组 /
    #    热电偶端子 / IC 的功能角色……）。属性证据（频率值、封装、型号族、引脚
    #    落点拓扑）比模板匹配更硬，故先于模板命中；不命中再交给模板。
    identity = role_rules.apply_identity_rules(board, pin_nets)
    if identity is not None:
        return identity

    # 把 ir.blocks 按"具体度"分桶：
    # - 宽泛块（rc/lc filter、inverting-amplifier 等仅靠拓扑易误命中）放最后兜底
    # - 具体块（有明确功能语义：shunt、snubber、resonant、pull-up/down、divider 等）优先
    _GENERIC_BLOCK_PREFIXES = (
        "rc-high-pass-", "rc-low-pass-", "lc-pi-filter-",
        "voltage-divider-", "inverting-amplifier-", "opamp-unit-",
        "differential-comparator-", "sallen-key-",
        "nmos-low-side-switch-", "npn-low-side-switch-", "pmos-high-side-switch-",
        # bjt-npn-low-side-switch 同属"三极管通用角色"：器件角色规则
        # （role_rules 的 transistor 规则）给的是"型号 + B/E/C 引脚判定 + 基极串接
        # 电阻"，比模板块的三行 net 摘要更准，故让位给它。模板块仍然进 ir.blocks，
        # 覆盖率报告与板上结构分析不受影响。
        "bjt-npn-low-side-switch-",
        "buck-converter-",
    )
    focal_blocks = [b for b in ir.blocks if focal.id in b.component_ids]
    specific_blocks = [
        b for b in focal_blocks
        if not any(b.id.startswith(p) for p in _GENERIC_BLOCK_PREFIXES)
    ]
    generic_blocks = [
        b for b in focal_blocks
        if any(b.id.startswith(p) for p in _GENERIC_BLOCK_PREFIXES)
    ]

    def _block_to_result(b) -> RoleResult:
        role_label = None
        mapping = b.metadata.get("component_mapping") or {}
        for comp_role, comp_id in mapping.items():
            if comp_id == focal.id:
                role_label = comp_role
                break
        # 标题主名 = 模板给该成员的功能名（component.title），缺省用模板名。
        # 块里不同成员功能不同（Buck 块的输出电感 ≠ 芯片本身），不能一律冠模板名。
        head = _member_head_title(b.id.rsplit("-", 1)[0], role_label, focal)
        title = head if not role_label else f"{head} · {role_label}"
        summary = b.description or f"作为 {b.name} 的一部分"
        ev = [
            f"block: {b.id} (role={b.role})",
            f"members: {', '.join(b.component_ids)}",
        ]
        for p, n in zip(focal.pins, pin_nets, strict=False):
            ev.append(f"pin {p.number}({p.name or '-'}) -> {_net_label(board, n)}")
        return _semantic_details(
            board,
            pin_nets,
            b.id.rsplit("-", 1)[0],
            RoleResult(
                rule=f"primitive:{b.id}",
                title=title,
                summary=summary,
                evidence=ev,
                confidence="high",
            ),
        )

    # 1) 具体 primitives 块优先（shunt / snubber / resonant / pull-up/down 等）。
    # 单件采样块让位于包含它的完整功率级：0Ω 位既是潜在采样点，也可能
    # 已明确属于 Buck 等更大功能块，后者提供的成员角色更精确。
    if specific_blocks:
        has_multicomponent_block = any(len(b.component_ids) > 1 for b in specific_blocks)
        specific_blocks.sort(
            key=lambda b: (
                has_multicomponent_block
                and b.id.startswith("current-shunt-monitor-")
                and len(b.component_ids) == 1,
                len(b.component_ids),
                b.id,
            )
        )
        return _block_to_result(specific_blocks[0])

    # 毫欧电阻不是普通上下拉：通用 shunt 模板依赖两端 net role，
    # 一端为 unknown 时会漏判；此处沿用有防误报逻辑的既有采样判定，
    # 在宽松 pull-down 模板抢走之前先确认低边/地桥/串联采样语义。
    shunt = _rule_shunt(board, pin_nets)
    if shunt is not None:
        return shunt

    # 1.5) 器件角色规则（三极管 / 接插件 / IC 功能 / 基极限流电阻 …）：
    #      这些规则给的是**通用角色**，功能模板块给的是**电路身份**——
    #      元件已经是某个功能块的一部分时块更准确，故排在模板块之后。
    device_role = role_rules.apply_device_role_rules(board, pin_nets)
    if device_role is not None:
        return device_role

    # 2) 手写规则已删除，全部走 primitives 模板
    # （如需恢复，见 git history；上面仅补回模板尚不能覆盖的毫欧采样判定）

    # 2.5) 拓扑规则：模板覆盖不到的通用拓扑（晶振负载电容、基极限流电阻、
    #      电源降压二极管、电源轨分压上臂……）。先于 live match，
    #      因为这些规则的证据来自属性与邻接网络，比模糊的模板匹配更可靠。
    topology = role_rules.apply_topology_rules(board, pin_nets)
    if topology is not None:
        return topology

    # 2.6) 现场跑 primitives matcher 查 focal 元件
    # 触发条件：ir.blocks 没有具体命中（specific_blocks 为空）
    # 即便有 generic_blocks 也跑 live match —— generic 模板（voltage-divider/
    # inverting-amplifier 等）易误命中，让 specific 模板（opamp-feedback 等）优先
    if not specific_blocks:
        live = _match_focal_live(ir, focal, board, session=live_session)
        if live is not None:
            return live

    # 3) 宽泛 primitives 块兜底
    if generic_blocks:
        generic_blocks.sort(key=lambda b: (len(b.component_ids), b.id))
        return _block_to_result(generic_blocks[0])

    # 4) 最后兜底规则（证据弱，但好过"未命中"）
    last = role_rules.apply_last_rules(board, pin_nets)
    if last is not None:
        return last

    # fallback：连接摘要
    ev = []
    for p, n in zip(focal.pins, pin_nets, strict=False):
        ev.append(f"pin {p.number}({p.name or '-'}) -> {_net_label(board, n)}")
    kind_label = _KIND_LABELS.get(focal.device_kind.value, focal.device_kind.value)
    part = _normalize_part_text(focal.part_number or focal.raw_value or "")
    return RoleResult(
        rule="fallback",
        title=f"{kind_label} {part}".strip(),
        summary=(
            f"未命中自动定性规则；用 `cg query --conn {focal.id}` 看拓扑，"
            "或补充 KB 引脚数据"
        ),
        evidence=ev,
        confidence="low",
    )
