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
from dataclasses import dataclass, field

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

# part_number/pin_number -> pin_name；查不到返回 None（该 IC 引脚按 unknown 处理）
PinoutProvider = Callable[[Component], dict[str, str] | None]

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


@dataclass
class RoleResult:
    """一个规则命中结果。"""

    rule: str
    title: str  # 第 1 行功能定性，如 "反馈电阻（U7 LM321 同相放大器）"
    summary: str  # 一句作用说明（含关键数值）
    evidence: list[str] = field(default_factory=list)  # 拓扑证据行
    confidence: str = "medium"  # high / medium / low
    data: dict[str, object] = field(default_factory=dict)  # 机读附加数据


# ---------------------------------------------------------------------------
# IR 拓扑视图
# ---------------------------------------------------------------------------


class _Board:
    """焦点元件周围的拓扑查询视图。"""

    def __init__(self, ir: CircuitIR, focal: Component, provider: PinoutProvider):
        self.ir = ir
        self.focal = focal
        self.provider = provider
        self.pin2net = {c.pin_id: c.net_id for c in ir.connections}
        self.net_members: dict[str, set[str]] = {}
        for c in ir.connections:
            self.net_members.setdefault(c.net_id, set()).add(c.pin_id.rsplit(".", 1)[0])
        self.comp = {c.id: c for c in ir.components}
        self.net = {n.id: n for n in ir.nets}
        self._pinout_cache: dict[str, dict[str, str]] = {}

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


def _fmt_num(x: float) -> str:
    if abs(x - round(x)) < 1e-9:
        return str(int(round(x)))
    return f"{x:.2g}"


def _fmt_freq(x: float) -> str:
    for unit, div in (("MHz", 1e6), ("kHz", 1e3), ("Hz", 1.0)):
        if x >= div:
            return f"{_fmt_num(x / div)} {unit}"
    return f"{_fmt_num(x)} Hz"


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


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def infer_role(
    ir: CircuitIR,
    designator: str,
    pinout_provider: PinoutProvider,
) -> RoleResult | None:
    """对 designator 做功能定性；找不到元件返回 None。"""
    ids = {c.id: c for c in ir.components}
    focal = ids.get(designator)
    if focal is None:
        lowered = designator.lower()
        focal = next((c for c in ir.components if c.id.lower() == lowered), None)
    if focal is None:
        return None

    board = _Board(ir, focal, pinout_provider)
    pin_nets = [board.net_of_pin(p.id) for p in focal.pins]

    if len(focal.pins) == 2:
        kind = focal.device_kind.value
        rules = _RES_RULES if kind == "Resistor" else _CAP_RULES if kind == "Capacitor" else []
        for rule in rules:
            hit = rule(board, pin_nets)
            if hit is not None:
                return hit

    # fallback：连接摘要
    ev = []
    for p, n in zip(focal.pins, pin_nets, strict=False):
        ev.append(f"pin {p.number}({p.name or '-'}) -> {_net_label(board, n)}")
    kind_label = {
        "IC": "集成电路",
        "Inductor": "电感",
        "Mosfet": "MOSFET",
        "Diode": "二极管",
        "Connector": "接插件",
    }.get(focal.device_kind.value, focal.device_kind.value)
    part = focal.part_number or focal.raw_value or ""
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
