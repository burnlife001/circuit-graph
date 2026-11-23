"""通用功能定性规则 —— 属性证据 + 邻接拓扑证据驱动。

每条规则回答"这个元件在这块板子上干什么"，并给出**可解释证据**。

两条硬约束：

1. **不按板卡位号写特例，也不按具体 IC 型号写特例。**
   型号只作为证据文本出现（"IC2 LTC4054"），不作为判定条件。
2. **宁可未命中，不要乱定性。** 证据不足就返回 ``None``，让后续规则/兜底接手。

规则分两类：

* **属性规则**（:mod:`circuit_graph.role_attrs`）：器件"是什么"由自身属性决定——
  频率值=晶振、``0.5A`` 规格=保险丝、``8050``=NPN 三极管、``SEG/COM`` 屏=显示模组。
* **拓扑规则**：器件"干什么"由邻接网络决定——晶振负载电容、基极限流电阻、
  电源降压二极管、电源轨分压上臂。

``board`` 是 :class:`circuit_graph.role._Board` 的实例（鸭子类型，不 import
避免循环依赖）。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from circuit_graph import role_attrs as attrs
from circuit_graph.ir import Component, NetType, ResistanceValue
from circuit_graph.role_types import RoleResult, fmt_freq

# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

_RES_UNIT_OHMS = {"Ω": 1.0, "kΩ": 1e3, "MΩ": 1e6}


def _resistance_ohms(comp: Component) -> float | None:
    """焦点元件的阻值（Ω）；解析不出返回 None（与 role._resistance_ohms 同义，
    此处独立实现以维持 role_rules 不 import role 的鸭子类型约定）。"""
    v = comp.value
    if isinstance(v, ResistanceValue):
        return v.magnitude * _RES_UNIT_OHMS.get(v.unit, 1.0)
    return None



#: 常见 BJT 封装的引脚顺序（1=B, 2=E, 3=C）。TO-92 各家不一致，故不列。
_BJT_PINOUT: dict[str, tuple[str, str, str]] = {
    "SOT-23": ("B", "E", "C"),
    "SOT23": ("B", "E", "C"),
    "SOT-89": ("B", "E", "C"),
    "SOT89": ("B", "E", "C"),
    "SOT-223": ("B", "E", "C"),
    "SOT-323": ("B", "E", "C"),
}

_GROUND_NAME_RE = re.compile(r"^(GND\d*|PGND|AGND|DGND|CGND|VSS|VEE)$", re.IGNORECASE)

#: IC 的反馈采样脚：接这种脚的上分压电阻不是上拉，是反馈分压上臂。
_FEEDBACK_PIN_RE = re.compile(r"^(FB|FEEDBACK|VFB|ADJ|VSENSE|V_SENSE)$", re.IGNORECASE)


def _is_ground(board, net_id: str | None) -> bool:
    """地族网络（GND/AGND/CGND/VSS…）。"""
    if net_id is None:
        return False
    if board.net_kind(net_id) is NetType.GND:
        return True
    return bool(_GROUND_NAME_RE.match(board.net_name(net_id)))


def _is_rail(board, net_id: str | None) -> bool:
    """电源/电池轨网络。"""
    if net_id is None:
        return False
    if board.net_kind(net_id) is NetType.POWER:
        return True
    return attrs.is_rail_net(board.net_name(net_id))


def _ic_pins_on(board, net_id: str | None) -> list[tuple[Component, str, str | None]]:
    """网络上的 (IC, 引脚号, 引脚名)。"""
    out: list[tuple[Component, str, str | None]] = []
    if net_id is None:
        return out
    for ic, pins in board.ics_on_net(net_id):
        for pn in pins:
            out.append((ic, pn, board.pin_name(ic, pn)))
    return out


def _net_label(board, net_id: str | None) -> str:
    """网络显示名（带 IC 引脚注释）。"""
    name = board.net_name(net_id)
    if net_id is None:
        return "?"
    if _is_ground(board, net_id) or _is_rail(board, net_id):
        return name
    notes = []
    for ic, pn, pname in _ic_pins_on(board, net_id)[:2]:
        notes.append(f"{ic.id}.{pn} {pname}" if pname else f"{ic.id}.{pn}")
    return f"{name}（{'; '.join(notes)}）" if notes else name


def _ends(board, pin_nets: list[str | None]) -> list[str]:
    """焦点元件两端网络 id（跳过 None）。"""
    return [n for n in pin_nets if n is not None]


def _parts_on(board, net_id: str | None, kinds: tuple[str, ...]) -> list[Component]:
    """网络上属于指定 device kind 的元件。"""
    if net_id is None:
        return []
    return [m for m in board.members(net_id) if m.device_kind.value in kinds]


def _crystals(board) -> list[Component]:
    """板上所有晶振（按属性判定）。"""
    return [
        c
        for c in board.ir.components
        if attrs.classify(c).kind == attrs.ATTR_CRYSTAL
    ]


def _bjts(board) -> list[Component]:
    """板上所有三极管（按属性判定）。"""
    return [
        c
        for c in board.ir.components
        if attrs.classify(c).kind in (attrs.ATTR_NPN, attrs.ATTR_PNP)
    ]


def _crystal_pins_on(board, net_id: str | None) -> list[Component]:
    """网络上接了晶振**时钟引脚**的晶振元件。

    晶振的外壳/地脚常接 GND；接地的那只脚不是时钟节点，不能据此判定
    "负载电容"，否则板上所有去耦电容都会被误判。
    """
    if net_id is None:
        return []
    if _is_ground(board, net_id):
        return []
    pins = {p.id for c in board.members(net_id) for p in c.pins}
    return [c for c in _crystals(board) if any(p.id in pins for p in c.pins)]


# ---------------------------------------------------------------------------
# 三极管引脚角色
# ---------------------------------------------------------------------------


@dataclass
class _BjtPins:
    """三极管的基/射/集引脚号（按封装约定；判不出时为 None）。"""

    base: str | None = None
    emitter: str | None = None
    collector: str | None = None
    source: str = ""  # 判定依据说明


def _bjt_package_pins(bjt: Component) -> _BjtPins | None:
    """按封装约定（SOT-23/89/223/323 → 1=B, 2=E, 3=C）给出引脚角色。"""
    fp = (bjt.footprint or "").upper().replace("-", "").replace("_", "")
    order = _BJT_PINOUT.get(fp)
    if order is None:
        return None
    nums = sorted(bjt.pins, key=lambda p: _pin_sort_key(p.number))
    if len(nums) < 3:
        return None
    base, emitter, collector = order
    return _BjtPins(
        base=nums[0].number if base == "B" else None,
        emitter=nums[1].number if emitter == "E" else None,
        collector=nums[2].number if collector == "C" else None,
        source=f"封装约定 {fp}: 1=B 2=E 3=C",
    )


def _pin_sort_key(number: str) -> tuple[int, str]:
    return (int(number), "") if number.isdigit() else (9999, number)


def _bjt_pins(board, bjt: Component) -> _BjtPins:
    """判定三极管引脚角色：先按封装约定，再用拓扑兜底。"""
    by_package = _bjt_package_pins(bjt)
    if by_package is not None:
        return by_package

    facts = attrs.classify(bjt)
    nets = {p.number: board.net_of_pin(p.id) for p in bjt.pins}
    emitter = None
    for pn, nid in nets.items():
        if facts.kind == attrs.ATTR_NPN and _is_ground(board, nid):
            emitter = pn
            break
        if facts.kind == attrs.ATTR_PNP and _is_rail(board, nid):
            emitter = pn
            break
    rest = [pn for pn in nets if pn != emitter]
    base = collector = None
    if len(rest) == 2:
        scored = sorted(
            rest,
            key=lambda pn: (
                0 if _net_has_bias_resistor(board, nets[pn]) else 1,
                0 if not _is_rail(board, nets[pn]) else 1,
                pn,
            ),
        )
        base, collector = scored[0], scored[1]
    return _BjtPins(base=base, emitter=emitter, collector=collector, source="拓扑推断")


def _net_has_bias_resistor(board, net_id: str | None) -> bool:
    """网络上是否有电阻（基极偏置通路的粗判据）。"""
    return bool(_parts_on(board, net_id, ("Resistor",)))


# ---------------------------------------------------------------------------
# 属性规则 —— "这是什么器件"
# ---------------------------------------------------------------------------


def rule_crystal(board, pin_nets) -> RoleResult | None:
    """晶振：频率值 + 相邻 IC 的时钟引脚。"""
    facts = attrs.classify(board.focal)
    if facts.kind != attrs.ATTR_CRYSTAL:
        return None
    freq = facts.value
    title = f"晶振（{fmt_freq(freq)}）" if freq else "晶振（石英谐振器）"

    ev = [f"{board.focal.id} {board.value_text(board.focal)}：{title}"]
    clock_notes: list[str] = []
    for nid in _ends(board, pin_nets):
        name = board.net_name(nid)
        for ic, pn, pname in _ic_pins_on(board, nid):
            if attrs.is_crystal_net(name) or _is_clock_pin_name(pname):
                clock_notes.append(f"{ic.id}.{pn}" + (f" {pname}" if pname else ""))
    if clock_notes:
        ev.append("时钟引脚：" + ", ".join(clock_notes))
    else:
        ev.append("时钟引脚：未识别（KB 引脚名缺失）")

    summary = (
        "为板上提供时钟/基准频率"
        if clock_notes
        else "为板上提供时钟/基准频率（相邻 IC 的时钟引脚未识别）"
    )
    return RoleResult(
        rule="crystal",
        title=title,
        summary=summary,
        evidence=ev,
        confidence="high" if freq else "medium",
        data={"frequency_hz": freq},
    )


def _is_clock_pin_name(pname: str | None) -> bool:
    """KB 引脚名是否像时钟/晶振引脚。"""
    if not pname:
        return False
    return bool(re.search(r"XTAL|XIN|XOUT|OSC|CLK|32K", pname, re.IGNORECASE))


def rule_fuse(board, pin_nets) -> RoleResult | None:
    """熔断器：额定电流规格 + 所在通路。"""
    facts = attrs.classify(board.focal)
    if facts.kind != attrs.ATTR_FUSE:
        return None
    rating = facts.detail or ""
    title = f"保险丝（{rating}）" if rating else "保险丝（熔断器）"
    ev = [
        f"{board.focal.id} {board.value_text(board.focal)}：熔断器规格 {rating or '未标注'}",
        "通路：" + " ↔ ".join(_net_label(board, n) for n in _ends(board, pin_nets)),
    ]
    return RoleResult(
        rule="fuse",
        title=title,
        summary="串在供电路径里，过流时熔断，保护后级不被打死",
        evidence=ev,
        confidence="medium",
        data={"rating": rating or None},
    )


def rule_display_module(board, pin_nets) -> RoleResult | None:
    """外接显示模组：SEG/COM 网络由某个 IC 驱动。"""
    facts = attrs.classify(board.focal)
    if facts.kind != attrs.ATTR_DISPLAY:
        return None
    seg_nets = [
        n for n in _ends(board, pin_nets) if attrs.is_segment_net(board.net_name(n))
    ]
    if len(seg_nets) < 2:
        return None
    drivers: list[str] = []
    seg_names: list[str] = []
    com_names: list[str] = []
    for nid in seg_nets:
        name = board.net_name(nid)
        (com_names if name.upper().startswith("COM") else seg_names).append(name)
        for ic, pins in board.ics_on_net(nid):
            if ic.id not in drivers:
                drivers.append(ic.id)
    title = "外接 LCD 显示模组"
    summary = (
        f"由 {', '.join(drivers)} 驱动 "
        f"{len(seg_names)} 个段（SEG）与 {len(com_names)} 个公共端（COM）"
        if drivers
        else f"接入 {len(seg_names)} 个段（SEG）与 {len(com_names)} 个公共端（COM）"
    )
    ev = [
        f"{board.focal.id} {board.value_text(board.focal)}：{title}",
        "段/公共端网络：" + ", ".join(seg_names + com_names),
        "驱动 IC：" + (", ".join(drivers) or "未识别"),
    ]
    return RoleResult(
        rule="display_module",
        title=title,
        summary=summary,
        evidence=ev,
        confidence="high" if len(seg_nets) >= 4 else "medium",
        data={"seg_nets": len(seg_names), "com_nets": len(com_names), "drivers": drivers},
    )


def rule_thermocouple_terminal(board, pin_nets) -> RoleResult | None:
    """热电偶输入端子：器件属性是热电偶 + 接到 T+/T- 形式的网络。"""
    facts = attrs.classify(board.focal)
    if facts.kind != attrs.ATTR_THERMOCOUPLE:
        return None
    # 去重：多个引脚落在同一 T+ 网上时只报告一次
    seen: set[str] = set()
    tc_nets = [
        n
        for n in _ends(board, pin_nets)
        if attrs.is_thermocouple_net(board.net_name(n))
        and not (n in seen or seen.add(n))
    ]
    if not tc_nets:
        return None
    ev = [f"{board.focal.id} {board.value_text(board.focal)}：热电偶输入端子"]
    ic_notes: list[str] = []
    for nid in tc_nets:
        name = board.net_name(nid)
        pins = _ic_pins_on(board, nid)
        targets = ", ".join(f"{i.id}.{p}" for i, p, _ in pins)
        ev.append(f"热电偶信号：{name}" + (f" → {targets}" if pins else ""))
        for ic, pn, pname in pins:
            if pname and re.search(r"^[+-]?(?:TC|TH|K)\s*[+-]?$", pname, re.IGNORECASE):
                ic_notes.append(f"{ic.id}.{pn} {pname}")
    return RoleResult(
        rule="thermocouple_terminal",
        title="热电偶输入端子",
        summary=(
            f"外接热电偶的接线端子，信号送 {ic_notes[0].split()[0]} 等测温电路"
            if ic_notes
            else "外接热电偶的接线端子，信号送测温 IC 采集"
        ),
        evidence=ev,
        confidence="high" if ic_notes else "medium",
    )


def rule_speaker(board, pin_nets) -> RoleResult | None:
    """蜂鸣器/扬声器：作为负载被三极管或 IC 驱动。"""
    facts = attrs.classify(board.focal)
    if facts.kind != attrs.ATTR_SPEAKER:
        return None
    drivers: list[str] = []
    for nid in _ends(board, pin_nets):
        for m in board.members(nid):
            if m.id == board.focal.id:
                continue
            if m.device_kind.value in ("Mosfet", "IC") or attrs.classify(m).kind in (
                attrs.ATTR_NPN,
                attrs.ATTR_PNP,
            ):
                drivers.append(m.id)
    ev = [
        f"{board.focal.id} {board.value_text(board.focal)}：蜂鸣器/扬声器（负载）",
        "驱动端：" + (", ".join(drivers) or "未识别"),
        "接入网络：" + ", ".join(board.net_name(n) for n in _ends(board, pin_nets)),
    ]
    return RoleResult(
        rule="speaker",
        title="蜂鸣器 / 扬声器（负载）",
        summary="发声负载，由前级三极管/IC 驱动通电",
        evidence=ev,
        confidence="medium",
        data={"drivers": drivers},
    )


def rule_battery(board, pin_nets) -> RoleResult | None:
    """电池：正极进电池轨、负极接地。"""
    facts = attrs.classify(board.focal)
    if facts.kind != attrs.ATTR_BATTERY:
        return None
    pos = neg = ""
    for nid in _ends(board, pin_nets):
        name = board.net_name(nid)
        if _is_ground(board, nid):
            neg = name
        elif attrs.is_battery_net(name) or _is_rail(board, nid):
            pos = name
    ev = [
        f"{board.focal.id} {board.value_text(board.focal)}：电池（主电源）",
        f"正极：{pos or '未识别'}",
        f"负极：{neg or '未识别'}",
    ]
    return RoleResult(
        rule="battery",
        title="电池（主电源）",
        summary="板上主电源，给后级供电",
        evidence=ev,
        confidence="medium",
    )


def rule_switch(board, pin_nets) -> RoleResult | None:
    """拨码/按键开关：多路触点接到信号网络。"""
    facts = attrs.classify(board.focal)
    if facts.kind != attrs.ATTR_SWITCH:
        return None
    nets = [board.net_name(n) for n in _ends(board, pin_nets)]
    ev = [
        f"{board.focal.id} {board.value_text(board.focal)}：拨码/按键开关",
        "触点网络：" + ", ".join(nets),
    ]
    return RoleResult(
        rule="switch",
        title="拨码开关（配置 / 模式选择）",
        summary="人工设定的开关，用触点把信号接到目标网络",
        evidence=ev,
        confidence="medium",
    )


def rule_transistor(board, pin_nets) -> RoleResult | None:
    """三极管：NPN 低边开关 / PNP 高边开关。"""
    facts = attrs.classify(board.focal)
    if facts.kind not in (attrs.ATTR_NPN, attrs.ATTR_PNP):
        return None
    is_npn = facts.kind == attrs.ATTR_NPN
    pins = _bjt_pins(board, board.focal)
    nets = {p.number: board.net_name(board.net_of_pin(p.id)) for p in board.focal.pins}
    ev = [
        f"{board.focal.id} {board.value_text(board.focal)}："
        f"{'NPN' if is_npn else 'PNP'} 三极管（型号 {facts.detail}）",
    ]
    for pn in sorted(nets, key=_pin_sort_key):
        role = ""
        if pn == pins.base:
            role = "基极 B"
        elif pn == pins.emitter:
            role = "发射极 E"
        elif pn == pins.collector:
            role = "集电极 C"
        ev.append(f"pin{pn}（{role or '?'}）→ {nets[pn]}")
    if pins.source:
        ev.append("引脚判定：" + pins.source)

    base_res = _base_resistor_of(board, board.focal, pins)
    if base_res is not None:
        ev.append(
            f"基极串接：{base_res.id}({board.value_text(base_res)}) "
            f"↔ {board.net_name(board.net_of_pin(base_res.pins[0].id))}"
            if base_res.pins
            else f"基极串接：{base_res.id}"
        )
    title = "NPN 三极管（低边开关 / 驱动）" if is_npn else "PNP 三极管（高边开关）"
    return RoleResult(
        rule="transistor",
        title=title,
        summary=(
            "导通时把负载接到地（低边驱动）" if is_npn else "导通时把负载接到正电源（高边开关）"
        ),
        evidence=ev,
        confidence="high" if pins.emitter else "medium",
        data={"type": facts.kind, "base": pins.base, "emitter": pins.emitter},
    )


def _base_resistor_of(board, bjt: Component, pins: _BjtPins) -> Component | None:
    """三极管基极串接的电阻。"""
    if pins.base is None:
        return None
    net_id = None
    for p in bjt.pins:
        if p.number == pins.base:
            net_id = board.net_of_pin(p.id)
    for r in _parts_on(board, net_id, ("Resistor",)):
        return r
    return None


# ---------------------------------------------------------------------------
# IC 功能规则 —— 按引脚落点拓扑判定"这颗 IC 是干什么的"
# ---------------------------------------------------------------------------


@dataclass
class _IcFacts:
    """IC 的引脚落点摘要。"""

    gnd_pins: list[str]
    power_pins: list[str]
    battery_pins: list[str]
    seg_pins: list[str]
    com_pins: list[str]
    i2c_pins: list[str]
    spi_pins: list[str]
    tc_pins: list[str]
    osc_pins: list[str]
    int_pins: list[str]
    rst_pins: list[str]
    status_pins: list[str]


#: KB 器件类别里能证实"存储器"的关键词（只认类别字段，不猜型号、不读自由文本）
_MEMORY_CATEGORY_KEYWORDS = ("memory", "flash", "eeprom", "fram", "存储", "闪存", "记忆体")


def _kb_says_memory(facts: dict[str, str]) -> bool:
    """KB 器件类别（category/subcategory）是否明确指向存储器。

    只看 KB 里结构化的类别字段；型号名硬编码（如某个 Flash 具体型号）一律
    不做——拓扑证据不允许升级成器件身份。
    """
    if not facts:
        return False
    text = " ".join(
        str(facts.get(key) or "") for key in ("category", "subcategory")
    ).lower()
    return any(kw in text for kw in _MEMORY_CATEGORY_KEYWORDS)


def _ic_facts(board) -> _IcFacts:
    """汇总焦点 IC 各引脚落在什么性质的网上。"""
    return _ic_facts_for(board, board.focal)


#: PWM 输出网络的网名词表：MCU 用它驱动功率级（半桥/全桥）的开关管。
_PWM_NET_RE = re.compile(r"^\w*PWMN?$", re.IGNORECASE)


def _pwm_output_nets(board, ic: Component) -> list[str]:
    """焦点 IC 落在 PWM 命名网络上的引脚网络（去重、保序）。"""
    out: list[str] = []
    for p in ic.pins:
        net = board.net_name(board.net_of_pin(p.id))
        if _PWM_NET_RE.match(net) and net not in out:
            out.append(net)
    return out


def _board_application_hint(board, pwm_nets: list[str]) -> str | None:
    """板级应用标注：无线充电发射板上的主控 = "无线充电主控"。

    判据：板上有无线充发射线圈（值不是电感量、串在谐振电容与桥臂之间），
    且焦点 IC 有多路 PWM 输出——它就是驱动发射功率级的那颗主控。
    两条都齐备才给标注，避免把普通 MCU 也叫成无线充电主控。
    """
    if len(pwm_nets) < 4:
        return None
    if not _board_has_wireless_coil(board):
        return None
    return "无线充电主控"


def _board_has_wireless_coil(board) -> bool:
    """板上有无线充发射线圈（扫描全部电感件）。"""
    for comp in board.ir.components:
        if comp.device_kind.value != "Inductor":
            continue
        if _wireless_coil_evidence(board, comp) is not None:
            return True
    return False


def rule_ic_function(board, pin_nets) -> RoleResult | None:
    """IC 功能定性：按引脚落点拓扑判定（热电偶/段码驱动/RTC/SPI 存储/充电 IC/LDO/MCU）。"""
    if board.focal.device_kind.value != "IC":
        return None
    facts = _ic_facts(board)
    ic = board.focal
    part = ic.part_number or ic.raw_value or ""
    label = f"{ic.id} {part}".strip()

    def _mk(title: str, summary: str, ev: list[str], confidence: str, data: dict) -> RoleResult:
        return RoleResult(
            rule="ic_function",
            title=title,
            summary=summary,
            evidence=ev,
            confidence=confidence,
            data=data,
        )

    ev_pins = [
        f"pin{p.number} → {board.net_name(board.net_of_pin(p.id))}"
        for p in ic.pins
    ]

    # 1) 热电偶测温 IC：引脚落在 T+/T- 形式的网络
    if facts.tc_pins:
        tc_nets = [
            board.net_name(board.net_of_pin(next(p.id for p in ic.pins if p.number == pn)))
            for pn in facts.tc_pins
        ]
        return _mk(
            f"热电偶测温转换 IC（{label}）",
            "把热电偶的毫伏信号数字化后经 SPI/输出端送主控",
            [f"{label}：热电偶输入 {', '.join(tc_nets)}", *ev_pins],
            "high",
            {"function": "thermocouple_adc"},
        )

    # 2) LCD 段码驱动 IC：多个 SEG/COM 引脚
    if len(facts.seg_pins) + len(facts.com_pins) >= 4:
        return _mk(
            f"LCD 段码驱动 IC（{label}）",
            f"驱动 {len(facts.seg_pins)} 个段（SEG）与 {len(facts.com_pins)} 个公共端（COM）",
            [f"{label}：SEG 引脚 {facts.seg_pins}，COM 引脚 {facts.com_pins}", *ev_pins],
            "high",
            {"function": "lcd_segment_driver"},
        )

    # 3) MCU：引脚多 + 接晶振（先于 RTC 判定：MCU 也带 I2C 与中断脚）
    if len(ic.pins) >= 10 and len(facts.osc_pins) >= 2:
        extra = f"，复位引脚 pin{','.join(facts.rst_pins)}" if facts.rst_pins else ""
        app = _board_application_hint(board, _pwm_output_nets(board, ic))
        return _mk(
            f"主控 MCU（{app or label}）",
            f"整机主控，晶振提供时钟{extra}",
            [
                f"{label}：{len(ic.pins)} 个引脚，晶振引脚 {facts.osc_pins}",
                *ev_pins,
            ],
            "high",
            {"function": "mcu"},
        )

    # 3b) MCU（板上没有外部晶振）：用内部 RC 振荡器时 OSC 脚被复用成 SDA/SCL，
    #     永远命不中 3) 分支。改用"多路 PWM 输出 + 大量引脚"认 MCU：
    #     ≥32 脚且 ≥4 路 PWM 命名网络 —— 只有驱动功率级的整机主控才有这个规模。
    pwm_nets = _pwm_output_nets(board, ic)
    if len(ic.pins) >= 32 and len(pwm_nets) >= 4:
        app = _board_application_hint(board, pwm_nets)
        return _mk(
            f"主控 MCU（{app or label}）",
            f"整机主控，{len(pwm_nets)} 路 PWM 驱动功率级"
            + (f"（{app}）" if app else ""),
            [
                f"{label}：{len(ic.pins)} 个引脚，PWM 输出 {pwm_nets}",
                *ev_pins,
            ],
            "high",
            {"function": "mcu"},
        )

    # 4) RTC：I2C + 中断 + 接晶振
    if len(facts.i2c_pins) >= 2 and len(facts.osc_pins) >= 2:
        extra = f"，另有中断输出 pin{','.join(facts.int_pins)}" if facts.int_pins else ""
        return _mk(
            f"实时时钟芯片 RTC（{label}）",
            f"由 32.768kHz 晶振计时，经 I2C 对外提供日期时间{extra}",
            [
                f"{label}：I2C 引脚 {facts.i2c_pins}，晶振引脚 {facts.osc_pins}",
                *ev_pins,
            ],
            "high",
            {"function": "rtc"},
        )

    # 4) SPI 总线：MOSI + MISO(+SCK) 只能证明"SPI 外设 IC"。是否存储器要靠
    #    KB 的器件类别证实——SPI ADC/传感器/RTC 同样走这组信号线，拓扑本身
    #    证明不了"存储器"，更证明不了"存固件"。
    spi_nets = {
        board.net_name(board.net_of_pin(next(p.id for p in ic.pins if p.number == pn)))
        for pn in facts.spi_pins
    }
    if any(n.upper().startswith(("MOSI", "MO", "MI", "DI", "SDI")) for n in spi_nets) and any(
        n.upper().startswith(("MISO", "MI", "DO", "SDO")) for n in spi_nets
    ):
        if _kb_says_memory(board.component_facts(ic)):
            return _mk(
                f"SPI 串行存储器 / Flash（{label}）",
                "经 SPI 与主控交换数据，存固件与配置参数",
                [f"{label}：SPI 网络 {sorted(spi_nets)}", *ev_pins],
                "high",
                {"function": "spi_memory"},
            )
        return _mk(
            f"SPI 外设 IC（{label}）",
            "经 SPI 总线与主控交换数据；具体是存储器、ADC 还是其他外设，"
            "需 KB 器件类别证实",
            [f"{label}：SPI 网络 {sorted(spi_nets)}", *ev_pins],
            "medium",
            {"function": "spi_peripheral"},
        )

    # 5) 电池充电 IC：电池脚 + 输入电源脚 + 地脚 + 状态脚
    if (
        facts.battery_pins
        and facts.power_pins
        and facts.gnd_pins
        and len(ic.pins) <= 8
    ):
        batt_net = board.net_name(
            board.net_of_pin(next(p.id for p in ic.pins if p.number == facts.battery_pins[0]))
        )
        in_net = board.net_name(
            board.net_of_pin(next(p.id for p in ic.pins if p.number == facts.power_pins[0]))
        )
        extra = f"，状态输出 pin{','.join(facts.status_pins)}" if facts.status_pins else ""
        return _mk(
            f"锂电池充电 IC（{label}）",
            f"把 {in_net} 的输入电源经内部线性稳压给 {batt_net} 充电{extra}",
            [
                f"{label}：电池脚 pin{facts.battery_pins[0]} → {batt_net}，"
                f"输入脚 pin{facts.power_pins[0]} → {in_net}，地脚 pin{facts.gnd_pins[0]}",
                *ev_pins,
            ],
            "high",
            {"function": "battery_charger"},
        )

    # 6) LDO 稳压器：两个电源脚 + 一个地脚，且引脚很少
    if (
        len(facts.power_pins) == 2
        and len(facts.gnd_pins) == 1
        and len(ic.pins) <= 5
    ):
        volt = []
        for pn in facts.power_pins:
            nid = board.net_of_pin(next(p.id for p in ic.pins if p.number == pn))
            volt.append((pn, board.net_name(nid), _net_volts(board.net_name(nid))))
        volt.sort(key=lambda t: (t[2] if t[2] is not None else -1))
        if volt[0][2] is not None and volt[1][2] is not None and volt[1][2] > volt[0][2]:
            summary = f"把 {volt[1][1]} 稳压成 {volt[0][1]} 给板子供电"
        else:
            summary = "一路电源变换后给板子供电"
        return _mk(
            "LDO 稳压器",
            f"{label}：" + summary,
            [
                f"{label}：输入 pin{volt[1][0]} → {volt[1][1]}，"
                f"输出 pin{volt[0][0]} → {volt[0][1]}",
                *ev_pins,
            ],
            "high",
            {"function": "ldo"},
        )

    return None


def _charger_ics(board) -> list[Component]:
    """板上的电池充电 IC（按引脚落点拓扑判定）。"""
    out: list[Component] = []
    for ic in board.ir.components:
        if ic.device_kind.value != "IC":
            continue
        facts = _ic_facts_for(board, ic)
        if (
            facts.battery_pins
            and facts.power_pins
            and facts.gnd_pins
            and len(ic.pins) <= 8
        ):
            out.append(ic)
    return out


def _ic_facts_for(board, ic: Component) -> _IcFacts:
    """汇总指定 IC 的引脚落点（与 :func:`_ic_facts` 同逻辑，但可指定 IC）。"""
    facts = _IcFacts(
        gnd_pins=[], power_pins=[], battery_pins=[], seg_pins=[], com_pins=[],
        i2c_pins=[], spi_pins=[], tc_pins=[], osc_pins=[], int_pins=[],
        rst_pins=[], status_pins=[],
    )
    for p in ic.pins:
        nid = board.net_of_pin(p.id)
        name = board.net_name(nid)
        upper = name.upper()
        pname = (board.pin_name(ic, p.number) or "").upper()
        if _is_ground(board, nid):
            facts.gnd_pins.append(p.number)
        # 电池轨（LION/VBATT…）同时也是轨名，但它是充电对象不是输入电源：
        # 计入 battery_pins 就不再计入 power_pins，否则"输入脚"会被写成电池脚。
        if _is_rail(board, nid) and not attrs.is_battery_net(name):
            facts.power_pins.append(p.number)
        if attrs.is_battery_net(name):
            facts.battery_pins.append(p.number)
        if re.match(r"^SEG\d*$", upper):
            facts.seg_pins.append(p.number)
        if re.match(r"^COM\d*$", upper):
            facts.com_pins.append(p.number)
        if re.match(r"^(SDA|SCL|I2C\w*)$", upper) or pname in ("SDA", "SCL"):
            facts.i2c_pins.append(p.number)
        if re.match(r"^(MOSI|MISO|MO|MI|DI|DO|SO|SDI|SDO|SCLK|SCK|CS|SS)\w*$", upper):
            facts.spi_pins.append(p.number)
        if attrs.is_thermocouple_net(name):
            facts.tc_pins.append(p.number)
        if _crystal_pins_on(board, nid):
            facts.osc_pins.append(p.number)
        if upper.startswith("INT"):
            facts.int_pins.append(p.number)
        if upper.startswith("RST") or pname in ("RST", "RESET", "NRST"):
            facts.rst_pins.append(p.number)
        if re.match(r"^(CHG|STAT|STATUS|LED|OC|CE)\w*$", upper):
            facts.status_pins.append(p.number)
    return facts


def _net_volts(name: str) -> float | None:
    """从网络名里解析电压（+4.2V → 4.2）。"""
    from circuit_graph.primitives import extract_volts

    return extract_volts(name)


# ---------------------------------------------------------------------------
# 拓扑规则 —— "这个元件在电路里干什么"
# ---------------------------------------------------------------------------


def rule_crystal_load_cap(board, pin_nets) -> RoleResult | None:
    """晶振负载/匹配电容：电容接在晶振引脚所在的网络上。"""
    if board.focal.device_kind.value != "Capacitor":
        return None
    for nid in _ends(board, pin_nets):
        crystals = _crystal_pins_on(board, nid)
        if not crystals:
            continue
        names = ", ".join(c.id for c in crystals)
        other = [n for n in _ends(board, pin_nets) if n != nid]
        ev = [
            f"{board.focal.id} {board.value_text(board.focal)}："
            f"接在晶振 {names} 的引脚网络 {board.net_name(nid)} 上",
            "另一端：" + ", ".join(board.net_name(n) for n in other),
        ]
        if other and _is_ground(board, other[0]):
            ev.append("另一端接地：与对侧电容共同决定晶振频率")
        return RoleResult(
            rule="crystal_load_cap",
            title=f"晶振负载电容（{board.net_name(nid)}）",
            summary="与对侧负载电容共同决定晶振频率，并提供起振条件",
            evidence=ev,
            confidence="high",
            data={"crystal": names},
        )
    return None


def rule_crystal_feedback_resistor(board, pin_nets) -> RoleResult | None:
    """晶振反馈电阻：并联在晶体两端，给内部振荡器提供起振增益。"""
    if board.focal.device_kind.value != "Resistor":
        return None
    ends = _ends(board, pin_nets)
    if len(ends) != 2:
        return None
    crystals_a = _crystal_pins_on(board, ends[0])
    crystals_b = _crystal_pins_on(board, ends[1])
    if not crystals_a or not crystals_b:
        return None
    names = ", ".join(sorted({c.id for c in crystals_a + crystals_b}))
    return RoleResult(
        rule="crystal_feedback_resistor",
        title=f"晶振反馈电阻（{names}）",
        summary="并联在晶体两端，给内部振荡器提供起振所需的直流偏置与负反馈",
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}："
            f"{board.net_name(ends[0])} ↔ {board.net_name(ends[1])}（晶体 {names}）"
        ],
        confidence="high",
        data={"crystal": names},
    )


def _same_block(board, a_id: str, b_id: str) -> bool:
    """两个元件是否被**同一个已识别功能块**收录。

    功能块（如 ``led-driver-switch``）把三极管与它的基极电阻一起认出来，
    说明块的身份比"这是个基极限流电阻"更准确——此时让位给块。
    """
    for blk in board.ir.blocks:
        if a_id in blk.component_ids and b_id in blk.component_ids:
            return True
    return False


def rule_bjt_base_resistor(board, pin_nets) -> RoleResult | None:
    """三极管基极通路上的电阻：泄放 / 下拉 / 限流 / 级联。"""
    if board.focal.device_kind.value != "Resistor":
        return None
    ends = _ends(board, pin_nets)
    if not ends:
        return None
    bjts = _bjts(board)
    if not bjts:
        return None

    best: tuple[int, RoleResult] | None = None
    for i, nid in enumerate(ends):
        other = ends[1 - i] if len(ends) > 1 else None
        for bjt in bjts:
            pin_here = next(
                (p.number for p in bjt.pins if board.net_of_pin(p.id) == nid), None
            )
            if pin_here is None:
                continue
            if _same_block(board, board.focal.id, bjt.id):
                # 三极管与本电阻同属一个功能块：块的身份更准，交给块。
                continue
            pins = _bjt_pins(board, bjt)
            if pin_here == pins.emitter:
                continue  # 直接接在发射极上的电阻不是基极电阻
            result = _classify_base_resistor(board, bjt, pins, pin_here, nid, other)
            if result is None:
                continue
            rank = result[0]
            if best is None or rank < best[0]:
                best = (rank, result[1])
    return best[1] if best else None


def _classify_base_resistor(
    board, bjt: Component, pins: _BjtPins, pin_here: str, nid: str, other: str | None
) -> tuple[int, RoleResult] | None:
    """判断电阻与三极管基极的关系，给出定性与优先级。"""
    emitter_net = None
    if pins.emitter:
        emitter_net = board.net_of_pin(
            next(p.id for p in bjt.pins if p.number == pins.emitter)
        )
    role = "基极" if pin_here == pins.base else "集电极"
    other_name = board.net_name(other) if other else "（未在网表中出现）"

    # 电阻接在**集电极**上的处理：1–5 号分支只对真正的基极电阻有意义。
    # 集电极网络本身就是电源轨（高边开关后级），电阻从轨上引向 IC 采样点
    # ——这是分压/采集拓扑，返回 None 让分压上臂、轨间电阻等规则接手。
    # 集电极在信号网上时，电阻才是集电极负载/上拉。
    if role == "集电极":
        if other is None or _is_rail(board, nid):
            return None
        return 5, RoleResult(
            rule="bjt_base_resistor",
            title=f"三极管集电极负载电阻（{bjt.id}）",
            summary="串在集电极回路上，把集电极电流转成电压或提供上拉",
            evidence=[
                f"{board.focal.id} {board.value_text(board.focal)}："
                f"{bjt.id}({board.value_text(bjt)}) 集电极 ↔ {other_name}"
            ],
            confidence="medium",
            data={"transistor": bjt.id, "relation": "collector_load"},
        )

    base_line = (
        f"{board.focal.id} {board.value_text(board.focal)}："
        f"{bjt.id}({board.value_text(bjt)}) {role} ↔ {other_name}"
    )
    ev = [base_line]

    # 1) 基极 ↔ 发射极：泄放/偏置电阻（PNP 关断保证）
    if emitter_net is not None and other == emitter_net:
        ev.append(
            f"接在 {bjt.id} 的基极与发射极之间：给基极提供确定的偏置电平"
            "（PNP 用它保证关断时基极不悬空）"
        )
        return 0, RoleResult(
            rule="bjt_base_resistor",
            title=f"三极管基极-发射极泄放电阻（{bjt.id}）",
            summary="把基极拉到发射极电平，保证三极管在无驱动时可靠关断",
            evidence=ev,
            confidence="high",
            data={"transistor": bjt.id, "relation": "base_emitter"},
        )

    # 2) 另一端在网表中缺失（该电阻只接了一端）
    if other is None:
        ev.append("该电阻在网表中只接了一端，另一端未出现在网表里")
        return 1, RoleResult(
            rule="bjt_base_resistor",
            title=f"三极管基极限流/偏置电阻（{bjt.id}，另一端缺失）",
            summary="串在基极通路上限制基极电流；另一端在网表中未体现",
            evidence=ev,
            confidence="medium",
            data={"transistor": bjt.id, "relation": "base_missing_end"},
        )

    # 3) 基极 ↔ 地：下拉
    if _is_ground(board, other):
        ev.append(f"另一端接地：给 {bjt.id} 的基极提供确定低电平（基极下拉）")
        return 2, RoleResult(
            rule="bjt_base_resistor",
            title=f"三极管基极下拉电阻（{bjt.id}）",
            summary="把基极拉到地，保证三极管无驱动时关断",
            evidence=ev,
            confidence="high",
            data={"transistor": bjt.id, "relation": "base_ground"},
        )

    # 4) 基极 ↔ IC 输出：限流/驱动
    ic_notes = [
        f"{ic.id}.{pn}" + (f" {pname}" if pname else "")
        for ic, pn, pname in _ic_pins_on(board, other)
    ]
    if ic_notes:
        ev.append("另一端接控制 IC：" + ", ".join(ic_notes))
        return 3, RoleResult(
            rule="bjt_base_resistor",
            title=f"三极管基极限流电阻（{bjt.id}）",
            summary="串在控制信号与基极之间，限制基极电流、保护 IC 输出",
            evidence=ev,
            confidence="high",
            data={"transistor": bjt.id, "relation": "base_driver"},
        )

    # 5) 基极 ↔ 另一只三极管：级联驱动
    others = [
        m.id
        for m in board.members(other)
        if attrs.classify(m).kind in (attrs.ATTR_NPN, attrs.ATTR_PNP)
    ]
    if others:
        ev.append("另一端接三极管：" + ", ".join(others))
        return 4, RoleResult(
            rule="bjt_base_resistor",
            title=f"三极管级联基极限流电阻（{bjt.id}）",
            summary="串在上一级三极管与本级基极之间，限制基极电流",
            evidence=ev,
            confidence="medium",
            data={"transistor": bjt.id, "relation": "base_cascade"},
        )

    ev.append(f"接在 {bjt.id} 的{role}上")
    return 6, RoleResult(
        rule="bjt_base_resistor",
        title=f"三极管基极电阻（{bjt.id}）",
        summary="串在三极管基极通路上，关系未定（泄放/下拉/限流都可能）",
        evidence=ev,
        confidence="low",
        data={"transistor": bjt.id, "relation": "unknown"},
    )


def rule_power_drop_diode(board, pin_nets) -> RoleResult | None:
    """电源通路上的降压二极管：电池轨 ↔ 下游供电网络。"""
    if board.focal.device_kind.value != "Diode":
        return None
    ends = _ends(board, pin_nets)
    if len(ends) != 2:
        return None
    for nid, other in ((ends[0], ends[1]), (ends[1], ends[0])):
        name = board.net_name(nid)
        if not attrs.is_battery_net(name):
            continue
        pins = _ic_pins_on(board, other)
        if not pins:
            continue
        ev = [
            f"{board.focal.id} {board.value_text(board.focal)}："
            f"串在供电通路里（{name} → {board.net_name(other)}）",
            "下游供电："
            + ", ".join(f"{ic.id}.{pn}" + (f" {pname}" if pname else "") for ic, pn, pname in pins),
            "二极管正向导通时下游电压比电池电压低约一个二极管压降",
        ]
        return RoleResult(
            rule="power_drop_diode",
            title=f"降压供电二极管（给 {pins[0][0].id} 供电）",
            summary="串在电池与下游供电之间，靠二极管压降把电压降到下游 IC 能承受的范围",
            evidence=ev,
            confidence="high" if len(pins) == 1 else "medium",
            data={"downstream": pins[0][0].id},
        )
    return None


def _divider_upper_arm(board, comp: Component) -> tuple[str, str, list] | None:
    """分压**上臂**判定：comp 两端里一端是电源轨、另一端接着 IC 采样脚。

    返回 ``(rail_end, node_end, pins)``；不是上臂则返回 ``None``。
    上臂与下臂共用这一份判定，"上臂是下臂的兄弟"才站得住。
    """
    ends = [n for n in (board.net_of_pin(p.id) for p in comp.pins) if n is not None]
    if len(ends) != 2:
        return None
    candidates: list[tuple[str, str]] = []
    for i, nid in enumerate(ends):
        other = ends[1 - i]
        if not _is_rail(board, nid):
            continue
        if not _ic_pins_on(board, other):
            continue
        candidates.append((nid, other))
    if not candidates:
        return None

    # 源轨优先取"名字里带电压值"的那一路（VBATT 这种电池名次之）。
    rail_end, node_end = sorted(
        candidates,
        key=lambda t: (
            0 if _net_volts(board.net_name(t[0])) is not None else 1,
            0 if not attrs.is_battery_net(board.net_name(t[0])) else 1,
        ),
    )[0]
    return rail_end, node_end, _ic_pins_on(board, node_end)


def rule_sense_divider_arm(board, pin_nets) -> RoleResult | None:
    """电源轨分压的上臂：轨 → IC 采样点，且同值的下臂在网表中完全缺失。"""
    if board.focal.device_kind.value != "Resistor":
        return None
    twin = _dangling_twin(board, board.focal)
    if twin is None:
        return None
    upper = _divider_upper_arm(board, board.focal)
    if upper is None:
        return None
    rail_end, node_end, pins = upper
    ev = [
        f"{board.focal.id} {board.value_text(board.focal)}："
        f"{board.net_name(rail_end)} ↔ {board.net_name(node_end)}",
        "采集点：" + ", ".join(
            f"{ic.id}.{pn}" + (f" {pname}" if pname else "") for ic, pn, pname in pins
        ),
        f"同封装同值电阻 {twin.id}({board.value_text(twin)}) 在网表中完全没有连接 —— "
        "疑似缺失的分压下臂",
    ]
    return RoleResult(
        rule="sense_divider_arm",
        title=f"分压电阻（上臂，{board.net_name(rail_end)} 采集分压）",
        summary="把电源轨电压经分压送到 IC 采样点；下臂在网表中未体现",
        evidence=ev,
        confidence="medium",
        data={"rail": board.net_name(rail_end), "twin": twin.id},
    )


def rule_feedback_divider_arm(board, pin_nets) -> RoleResult | None:
    """反馈分压上臂：电源轨 → IC 反馈脚（FB/ADJ/VSENSE），同节点另有电阻到地。

    宽泛的 pull-up 模板只看"一端电源、另一端任意网"，会把 FB 分压上臂误报成
    上拉电阻；此规则要求同时在位：电源轨 + IC 反馈脚 + 到地下臂，才认定分压。
    """
    if board.focal.device_kind.value != "Resistor":
        return None
    ends = _ends(board, pin_nets)
    if len(ends) != 2 or ends[0] == ends[1]:
        return None
    for rail_end, node_end in (ends, list(reversed(ends))):
        if not _is_rail(board, rail_end):
            continue
        fb_pins = [
            f"{ic.id}.{pn}" + (f" {pname}" if pname else "")
            for ic, pn, pname in _ic_pins_on(board, node_end)
            if _FEEDBACK_PIN_RE.match((pname or "").strip())
        ]
        if not fb_pins:
            continue
        lowers = sorted(
            (
                m
                for m in board.members(node_end)
                if m.id != board.focal.id
                and m.device_kind.value == "Resistor"
                and _has_pin_to_ground(board, m, node_end)
            ),
            key=lambda c: c.id,
        )
        if not lowers:
            continue
        lower = lowers[0]
        ev = [
            f"{board.focal.id} {board.value_text(board.focal)}："
            f"{board.net_name(rail_end)} ↔ {board.net_name(node_end)}",
            "反馈采样引脚：" + ", ".join(fb_pins),
            f"下臂 {lower.id} {board.value_text(lower)}："
            f"{board.net_name(node_end)} ↔ GND",
        ]
        return RoleResult(
            rule="feedback_divider_arm",
            title="反馈分压电阻（上臂）",
            summary=(
                f"与 {lower.id} 构成反馈分压：{board.net_name(rail_end)} 分压后"
                f"送入 {', '.join(fb_pins)}，设定/监测输出电压"
            ),
            evidence=ev,
            confidence="high",
            data={
                "rail": board.net_name(rail_end),
                "lower_arm": lower.id,
                "feedback_pins": fb_pins,
            },
        )
    return None


def _has_pin_to_ground(board, comp: Component, node_end: str) -> bool:
    """元件在 node_end 上有一脚，且另一脚接地。"""
    node_pins = set(board.pins_on_net(comp.id, node_end))
    if not node_pins:
        return False
    return any(
        p.number not in node_pins and _is_ground(board, board.net_of_pin(p.id))
        for p in comp.pins
    )


def rule_sense_divider_lower_arm(board, pin_nets) -> RoleResult | None:
    """网表里完全没有连接的电阻：疑似同值同封装兄弟分压上臂的**下臂**。

    网表导出不全时，下臂常常只剩一条元件记录、两个引脚子记录全丢，
    拓扑上什么都推不出来（只能报"未连接"）。但有一条旁证可查：
    同封装同值的兄弟电阻已经被判成"分压上臂"，而分压只差一条下臂。

    三条前提同时成立才敢猜，缺一即放弃（宁可未命中，不要乱定性）：
      1. 本元件在网表里一个引脚都没有（不是"没定性"，是"没连接"）；
      2. 同封装同值的电阻中**有且只有它**悬空——多个候选就说不清是哪个；
      3. 那只兄弟电阻本身满足上臂拓扑（电源轨 → IC 采样点）。
    """
    if board.focal.device_kind.value != "Resistor":
        return None
    if board.focal.pins:
        return None
    siblings = _same_value_siblings(board, board.focal)
    # 悬空的同值同封装电阻只能有本元件一个：多一个就说不清是哪个下臂。
    if any(not c.pins for c in siblings):
        return None
    for sib in siblings:
        if not sib.pins:
            continue
        upper = _divider_upper_arm(board, sib)
        if upper is None:
            continue
        rail_end, node_end, pins = upper
        ev = [
            f"{board.focal.id} {board.value_text(board.focal)}：引脚数 0（网表中无任何连接）",
            f"同封装同值电阻 {sib.id}({board.value_text(sib)}) 已被识别为分压上臂："
            f"{board.net_name(rail_end)} ↔ {board.net_name(node_end)}",
            "采集点：" + ", ".join(
                f"{ic.id}.{pn}" + (f" {pname}" if pname else "") for ic, pn, pname in pins
            ),
            "疑似该分压的下臂：网表未导出本元件的连接，需回查原理图确认",
        ]
        return RoleResult(
            rule="sense_divider_lower_arm",
            title=f"疑似分压电阻（下臂，{board.net_name(rail_end)} 采集分压）",
            summary=(
                f"网表里没有落到任何网络上；同值同封装的 {sib.id} 已是分压上臂，"
                "本元件疑似缺失的下臂（需回查原理图确认）"
            ),
            evidence=ev,
            confidence="low",
            data={"rail": board.net_name(rail_end), "upper_arm": sib.id},
        )
    return None


def _same_value_siblings(board, comp: Component) -> list[Component]:
    """同封装同值的电阻兄弟（不含 comp 自己）。"""
    value = _norm_value(board.value_text(comp))
    out: list[Component] = []
    for c in board.ir.components:
        if c.id == comp.id or c.device_kind.value != "Resistor":
            continue
        if (c.footprint or "") != (comp.footprint or ""):
            continue
        if _norm_value(board.value_text(c)) != value:
            continue
        out.append(c)
    return out


def _dangling_twin(board, comp: Component) -> Component | None:
    """找同封装同值、且在网表中**完全没有连接**的电阻（疑似缺失的分压下臂）。"""
    for c in _same_value_siblings(board, comp):
        if not c.pins:
            return c
    return None


def _norm_value(text: str) -> str:
    """值文本归一（压缩空白、去空格），便于比较两只"同值"电阻。"""
    return " ".join((text or "").split()).upper()


def rule_rail_resistor(board, pin_nets) -> RoleResult | None:
    """两路电源轨之间的电阻（负载分流 / 电压采集 / 隔离）。"""
    if board.focal.device_kind.value != "Resistor":
        return None
    ends = _ends(board, pin_nets)
    if len(ends) != 2:
        return None
    if not (_is_rail(board, ends[0]) and _is_rail(board, ends[1])):
        return None
    # 小阻值（≤10Ω，含 0Ω）串在两轨之间是**电流采样位**——"轨间电阻"的
    # 分压/隔离语义解释不了它，让位给 current-shunt-monitor 采样语义。
    r = _resistance_ohms(board.focal)
    if r is not None and r <= 10.0:
        return None
    ev = [
        f"{board.focal.id} {board.value_text(board.focal)}："
        f"{board.net_name(ends[0])} ↔ {board.net_name(ends[1])}",
    ]
    for nid in ends:
        pins = _ic_pins_on(board, nid)
        if pins:
            ev.append(
                f"{board.net_name(nid)} 接入："
                + ", ".join(f"{ic.id}.{pn}" for ic, pn, _ in pins)
            )
    return RoleResult(
        rule="rail_resistor",
        title=f"电源轨间电阻（{board.net_name(ends[0])} ↔ {board.net_name(ends[1])}）",
        summary="串在两路电源轨之间（常见用途：电压采集分压、负载分流或轨间隔离）",
        evidence=ev,
        confidence="low",
    )


# ---------------------------------------------------------------------------
# 接口 / 通用无源件规则
# ---------------------------------------------------------------------------


def rule_connector(board, pin_nets) -> RoleResult | None:
    """接口类型按属性判定（USB / JTAG / 串行通信 / 热电偶已在别处处理）。"""
    if board.focal.device_kind.value != "Connector":
        return None
    joined = " ".join(
        t for t in (board.focal.footprint, board.focal.raw_value, board.focal.part_number) if t
    ).upper()
    nets = [board.net_name(n) for n in _ends(board, pin_nets)]
    diff = [n for n in nets if n.upper() in ("D+", "D-")]

    kind: str | None = None
    if "USB" in joined:
        kind = "USB 接口"
    elif "JTAG" in joined:
        kind = "调试接口（JTAG）"
    elif "UART" in joined or "RS232" in joined or "RS485" in joined:
        kind = "串行通信接口（UART/RS232/RS485）"
    elif diff:
        kind = "USB（差分信号）接口"
    if kind is None:
        return None

    ev = [
        f"{board.focal.id} {board.value_text(board.focal)}：{kind}",
        "接入网络：" + ", ".join(nets),
    ]
    if diff:
        ev.append("差分对：" + ", ".join(diff))
    return RoleResult(
        rule="connector",
        title=f"{kind}（{board.focal.id}）",
        summary="板对外接口，" + ("接外部主机/外设" if "USB" in kind else "供调试或外设连接"),
        evidence=ev,
        confidence="medium",
    )


def rule_touch_key_ic(board, pin_nets) -> RoleResult | None:
    """电容式触摸按键 IC：有 TCH / TOUCH / 电极感应脚的按键控制器。"""
    if board.focal.device_kind.value != "IC":
        return None
    touch_pins: list[str] = []
    for p in board.focal.pins:
        pname = (board.pin_name(board.focal, p.number) or p.name or "").upper()
        if pname.startswith(("TCH", "TOUCH", "ELECTRODE", "TPAD", "KEY")):
            touch_pins.append(p.number)
    if not touch_pins:
        return None
    label = f"{board.focal.id} {board.focal.part_number or board.focal.raw_value or ''}".strip()
    ev = [
        f"{label}：触摸感应脚 pin{','.join(touch_pins)}",
    ]
    for p in board.focal.pins:
        nid = board.net_of_pin(p.id)
        ev.append(f"pin{p.number}（{p.name or '-'}）→ {board.net_name(nid)}")
    return RoleResult(
        rule="touch_key_ic",
        title="电容式触摸按键 IC",
        summary="电容式触摸按键控制器：感应电极上的电荷变化被它转换成按键事件",
        evidence=ev,
        confidence="high",
        data={"function": "touch_key_ic", "touch_pins": touch_pins},
    )


def rule_unconnected(board, pin_nets) -> RoleResult | None:
    """网表里没有任何连接的元件。"""
    if board.focal.pins:
        return None
    return RoleResult(
        rule="unconnected",
        title="未连接的元件（网表中无任何连接）",
        summary="该元件在网表里没有落到任何网络上：可能未画完、已悬空或网表不完整",
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}：引脚数 0",
            "需回查原理图确认连接",
        ],
        confidence="low",
    )


def rule_decoupling_cap(board, pin_nets) -> RoleResult | None:
    """电源轨 ↔ 地的电容 = 去耦电容。"""
    if board.focal.device_kind.value != "Capacitor":
        return None
    ends = _ends(board, pin_nets)
    if len(ends) != 2:
        return None
    gnd_end = next((n for n in ends if _is_ground(board, n)), None)
    rail_end = next((n for n in ends if _is_rail(board, n)), None)
    if gnd_end is None or rail_end is None:
        return None
    rail = board.net_name(rail_end)
    powered = [
        f"{ic.id}.{pn}"
        for ic, pins in board.ics_on_net(rail_end)
        for pn in pins
        if board.pin_category(ic, pn) == "power_in"
    ]
    ev = [
        f"{board.focal.id} {board.value_text(board.focal)}：{rail} ↔ {board.net_name(gnd_end)}",
    ]
    if powered:
        ev.append("供电引脚：" + ", ".join(powered[:3]))
    return RoleResult(
        rule="decoupling",
        title=f"去耦电容（{rail} ↔ {board.net_name(gnd_end)}）",
        summary=f"滤掉 {rail} 上的高频噪声，给本地电源退耦",
        evidence=ev,
        confidence="high",
    )


def _driver_members_on(board, net_id: str | None) -> list:
    """网络上的"驱动件"（三极管 / 接插件）——耦合电容负载侧的可指认证据。"""
    if net_id is None:
        return []
    return [
        m for m in board.members(net_id)
        if attrs.classify(m).kind in (attrs.ATTR_NPN, attrs.ATTR_PNP)
        or m.device_kind.value == "Connector"
    ]


def rule_coupling_cap(board, pin_nets) -> RoleResult | None:
    """交流耦合 / 隔直电容。

    两种形态：
    * 两个信号网络之间（典型：前级输出 → 后级输入）；
    * 电源轨与负载节点之间（把负载的直流电平与电源隔开）。
    """
    if board.focal.device_kind.value != "Capacitor":
        return None
    ends = _ends(board, pin_nets)
    if len(ends) != 2:
        return None
    if _is_ground(board, ends[0]) or _is_ground(board, ends[1]):
        return None  # 对地的是去耦/滤波，不是耦合
    rail_end = next((n for n in ends if _is_rail(board, n)), None)
    load_end = next((n for n in ends if n != rail_end), None) if rail_end else None
    if rail_end is not None and load_end is not None:
        # 轨↔负载形态：负载端必须有可指认的负载（IC 引脚 / 三极管 / 接插件）。
        # 只有"电源轨 + 一根串联电阻"的两个网（如反馈补偿网络 C+R）不够格——
        # 那是补偿/取样支路，不是给负载隔直。
        if not (_ic_pins_on(board, load_end) or _driver_members_on(board, load_end)):
            return None
    else:
        # 信号↔信号形态：两端都要有 IC / 驱动才断言"前后级耦合"。
        if not all(
            _ic_pins_on(board, n) or _driver_members_on(board, n) for n in ends
        ):
            return None
    ev = [
        f"{board.focal.id} {board.value_text(board.focal)}："
        + " → ".join(_net_label(board, n) for n in ends)
    ]
    if rail_end is not None and load_end is not None:
        drivers = [
            m.id
            for m in board.members(load_end)
            if attrs.classify(m).kind in (attrs.ATTR_NPN, attrs.ATTR_PNP)
            or m.device_kind.value == "Connector"
        ]
        ev.append(
            "负载节点：" + board.net_name(load_end)
            + (f"（驱动：{', '.join(drivers)}）" if drivers else "")
        )
        return RoleResult(
            rule="coupling",
            title="隔直 / 耦合电容（负载与电源隔离）",
            summary="阻断直流分量，让负载节点只通过交流/脉冲成分",
            evidence=ev,
            confidence="medium",
        )
    return RoleResult(
        rule="coupling",
        title="交流耦合 / 隔直电容",
        summary="隔直流通交流，把前级信号送进后级而不传递直流偏置",
        evidence=ev,
        confidence="medium",
    )


def rule_filter_cap(board, pin_nets) -> RoleResult | None:
    """对地电容 + 节点是**可识别电源轨** = 滤波 / 旁路。

    "任何对地电容都是滤波/旁路"是典型乱定性：复位 RC 的定时电容、触摸 IC
    的灵敏度电容、补偿网络电容都接到地。节点不是可识别电源轨时宁可不说
    （落 fallback），让工程师补模板/KB——防误报优先于防漏报。
    """
    if board.focal.device_kind.value != "Capacitor":
        return None
    ends = _ends(board, pin_nets)
    if len(ends) != 2:
        return None
    gnd_end = next((n for n in ends if _is_ground(board, n)), None)
    if gnd_end is None:
        return None
    node = next((n for n in ends if n != gnd_end), None)
    if node is None:
        return None
    if not _is_rail(board, node):
        return None
    return RoleResult(
        rule="filter_cap",
        title=f"滤波 / 旁路电容（{board.net_name(node)}）",
        summary="对地滤除该节点上的高频分量",
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}："
            f"{_net_label(board, node)} ↔ {board.net_name(gnd_end)}"
        ],
        confidence="medium",
    )


def rule_series_resistor(board, pin_nets) -> RoleResult | None:
    """串在信号通路里的电阻。

    两端都接 IC 是高置信信号串阻；一端接 IC、另一端只有本电阻的引出端
    也保留低置信判定。若另一端还有连接器、晶体管等**有源**器件，则可能是
    CC / 栅极等专用电阻，不能用宽泛的"串联电阻"覆盖。

    另一端只挂着无源件（R / C）时同样成立：上拉 + 去耦构成的中枢节点
    （如 MCU 的 RST）就是这种形状，那只串在 IC 信号脚与该节点之间的电阻
    正是信号通路上的串接电阻。但从电源轨 / 地出发的电阻是上拉、下拉或
    滤波支路，不是信号串阻，故排除。
    """
    if board.focal.device_kind.value != "Resistor":
        return None
    ends = _ends(board, pin_nets)
    if len(ends) != 2:
        return None
    ic_ends = 0
    desc: list[str] = []
    for nid in ends:
        pins = _ic_pins_on(board, nid)
        conns = [
            m.id for m in board.members(nid) if m.device_kind.value == "Connector"
        ]
        if pins:
            ic_ends += 1
        parts = [f"{ic.id}.{pn}" for ic, pn, _ in pins]
        if conns:
            parts.append("接插件 " + ", ".join(conns))
        if not parts:
            # 另一端只有无源件时把成员列出来：读者才看得到"串在 IC 信号脚
            # 与上拉/滤波中枢节点之间"这层结构。
            others = [m.id for m in board.members(nid) if m.id != board.focal.id]
            if others:
                parts.append("挂 " + ", ".join(others))
        desc.append(board.net_name(nid) + (f"（{'; '.join(parts)}）" if parts else ""))
    if ic_ends == 0:
        return None
    if ic_ends == 1:
        ic_net = next(nid for nid in ends if _ic_pins_on(board, nid))
        other_net = next(nid for nid in ends if nid != ic_net)
        others = [m for m in board.members(other_net) if m.id != board.focal.id]
        if others:
            # 另一端只允许无源件（R / C）：连接器、晶体管、IC 在另一端时，
            # 这只电阻多半是 CC / 栅极 / 基极等专用电阻，不能用宽泛的
            # "串联电阻"覆盖。
            if any(m.device_kind.value not in ("Resistor", "Capacitor") for m in others):
                return None
            # IC 端必须是信号网：从电源轨 / 地出发的电阻是上拉、下拉或滤波
            # 支路，不是"信号通路里的串联电阻"。
            if _is_rail(board, ic_net) or _is_ground(board, ic_net):
                return None
    return RoleResult(
        rule="series_signal",
        title="信号串联电阻",
        summary=(
            f"串在 {board.net_name(ends[0])} ↔ {board.net_name(ends[1])} 的信号通路中："
            "限流 / 阻抗匹配 / 便于调试割断"
        ),
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}：" + " → ".join(desc)
        ],
        confidence="medium" if ic_ends == 2 else "low",
    )


def rule_charger_prog_resistor(board, pin_nets) -> RoleResult | None:
    """充电电流编程电阻：接在电池充电 IC 的编程脚与地之间。"""
    if board.focal.device_kind.value != "Resistor":
        return None
    ends = _ends(board, pin_nets)
    if len(ends) != 2:
        return None
    gnd_end = next((n for n in ends if _is_ground(board, n)), None)
    if gnd_end is None:
        return None
    prog_end = next((n for n in ends if n != gnd_end), None)
    if prog_end is None:
        return None
    for charger in _charger_ics(board):
        facts = _ic_facts_for(board, charger)
        skip = set(facts.gnd_pins) | set(facts.battery_pins) | set(facts.status_pins)
        for p in charger.pins:
            if p.number in skip:
                continue
            if board.net_of_pin(p.id) != prog_end:
                continue
            return RoleResult(
                rule="charger_prog_resistor",
                title=f"充电电流编程电阻（{charger.id}）",
                summary=(
                    "接在充电 IC 的编程脚与地之间，阻值决定充电电流"
                    f"（{board.value_text(board.focal)}）"
                ),
                evidence=[
                    f"{board.focal.id} {board.value_text(board.focal)}："
                    f"{charger.id}.{p.number}（编程脚）↔ {board.net_name(gnd_end)}",
                    f"{charger.id} 被识别为电池充电 IC（电池脚 + 输入电源脚 + 地脚）",
                ],
                confidence="high",
                data={"charger": charger.id, "pin": p.number},
            )
    return None


# ---------------------------------------------------------------------------
# 探针位 / LED / 接口 / 跳线 —— 器件身份补充规则
# ---------------------------------------------------------------------------

#: 测试点位的常见值写法（TP1.5 / TP / TP3）
_TESTPOINT_VALUE_RE = re.compile(r"^TP(?:[\s\-_.]?\d+)*(?:\.\d+)?$", re.IGNORECASE)
#: 串口信号名（探针位挂在串口收发线上）
_UART_NET_RE = re.compile(
    r"^(?:RXD|TXD|URX|UTX|RXD_?\d*|TXD_?\d*|UART\w*|SERIAL\w*)$", re.IGNORECASE
)
#: 调试串口前缀（DBG_TXD / DEBUG_URX …）
_DEBUG_UART_RE = re.compile(r"^(?:DBG|DEBUG)[_-]", re.IGNORECASE)
#: LED 的颜色字样（网表里 LED 常只写颜色，不写型号）
_LED_COLOR_RE = re.compile(
    r"^(?:LED[\s\-_/]*)?(?:RED|GREEN|BLUE|WHITE|YELLOW|ORANGE|PURPLE)$", re.IGNORECASE
)
#: USB Type-C 连接器的写法
_TYPEC_RE = re.compile(r"TYPE[\s\-_]?C|USB[\s\-_]?C", re.IGNORECASE)
#: USB-C 的 CC 线网络名
_CC_NET_RE = re.compile(r"^CC\d*$", re.IGNORECASE)
#: USB-C CC 控制器侧网络名（CC1ID / CC2IO …）
_CC_CTRL_NET_RE = re.compile(r"^CC\d*(?:ID|IO)$", re.IGNORECASE)
#: SWD 调试口信号名（SWCLK / SWLCK 等常见拼法都收）
_SWD_SIGNAL_RE = re.compile(r"^SW(?:DIO|DIO|CLK|LCK|CK|DAT|AT)$", re.IGNORECASE)
#: 0Ω 跳线 / 电阻封装的电感位（网表里电感位常是 0R 或磁珠）
_RESISTOR_PACKAGE_RE = re.compile(
    r"^(?:0+(?:\.0+)?[RΩ]|0R0+|R00+|R[\s\-_][A-Za-z0-9]+)(?:[\s\-_][A-Za-z0-9]+)?$",
    re.IGNORECASE,
)


def _is_probe_point(comp: Component) -> bool:
    """单脚、网表未给器件类型的探针位（测试点）。"""
    if comp.device_kind.value != "Unknown" or len(comp.pins) != 1:
        return False
    texts = [str(t).strip() for t in (comp.raw_value, comp.part_number, comp.footprint) if t]
    return any(
        _TESTPOINT_VALUE_RE.match(t) or _UART_NET_RE.match(t) for t in texts
    )


def _is_voltage_probe_net(board, net_id: str | None) -> bool:
    """探针网是"被测电压节点"：网上同时挂着整流二极管与电容。

    典型：线圈/开关节点经二极管整流后的输出（VBAK_A 这类），电容是储能/
    滤波位（对地或跨到另一整流节点都算）——工程师在此放测试点就是为了量
    这个电压；普通信号线（RXD/SWD）和开关管驱动网没有"二极管+电容"组合。
    """
    if net_id is None:
        return False
    members = board.members(net_id)
    return any(m.device_kind.value == "Diode" for m in members) and any(
        m.device_kind.value == "Capacitor" for m in members
    )


def rule_test_point(board, pin_nets) -> RoleResult | None:
    """测试点（探针位）：单脚无源位，供示波器/万用表夹测。"""
    if not _is_probe_point(board.focal):
        return None
    net_id = pin_nets[0] if pin_nets else None
    net_name = board.net_name(net_id).strip()
    # 调试串口的前缀（DBG_/DEBUG_）先剥掉，再看是不是串口信号
    bare = _DEBUG_UART_RE.sub("", net_name, count=1)
    if _is_ground(board, net_id):
        title = "接地测试点"
        summary = "接在 GND 上的探针位：测板子地电位、当地参考点"
    elif _UART_NET_RE.match(bare):
        if bare != net_name:
            title = "调试串口测试点"
            summary = "调试串口收发线上的探针位"
        else:
            title = "串口测试点"
            summary = "串口收发线上的探针位"
    elif _is_voltage_probe_net(board, net_id):
        # 整流/储能节点（二极管 + 储能电容挂同一网）：测的是该节点电压。
        title = "电压测试点"
        summary = "整流/储能节点上的探针位：测该节点的直流电压"
    else:
        title = "测试点"
        summary = "供探针夹测量的引出点"
    return RoleResult(
        rule="test_point",
        title=title,
        summary=summary,
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}：单脚探针位 → {net_name}",
            "器件类型：网表未给（单脚位＝探针/测试点）",
        ],
        confidence="high",
        data={"function": "test_point"},
    )


def _is_led(comp: Component) -> bool:
    """二极管位上的 LED：值写颜色，或型号带 LED 前缀。"""
    if comp.device_kind.value != "Diode":
        return False
    texts = [str(t).strip() for t in (comp.raw_value, comp.part_number) if t]
    return any(
        _LED_COLOR_RE.match(t) or re.search(r"\bLED\b", t, re.IGNORECASE) for t in texts
    )


def rule_led_indicator(board, pin_nets) -> RoleResult | None:
    """LED 指示灯：二极管 + 限流电阻，由 GPIO 驱动或直接挂电源轨点亮。"""
    if not _is_led(board.focal):
        return None
    ends = _ends(board, pin_nets)
    ev = [f"{board.focal.id} {board.value_text(board.focal)}：LED 指示灯"]
    for nid in ends:
        parts = [
            f"{m.id}({board.value_text(m)})"
            for m in board.members(nid)
            if m.id != board.focal.id
        ]
        ev.append(f"→ {board.net_name(nid)}：{', '.join(parts) or '无'}")
    return RoleResult(
        rule="led_indicator",
        title="LED 指示灯",
        summary="状态指示发光二极管：与限流电阻串联，由 IO 或电源轨点亮",
        evidence=ev,
        confidence="high",
        data={"function": "led_indicator"},
    )


def rule_led_series_resistor(board, pin_nets) -> RoleResult | None:
    """LED 限流电阻：与 LED 串联，串在驱动信号/电源与 LED 之间。"""
    if board.focal.device_kind.value != "Resistor":
        return None
    ends = _ends(board, pin_nets)
    if len(ends) != 2:
        return None
    for i, nid in enumerate(ends):
        members = board.members(nid)
        leds = [m for m in members if _is_led(m)]
        if not leds:
            continue
        # 该网上除 LED 外只剩焦点电阻 → 它就是 LED 的限流电阻
        others = [m for m in members if m.id != board.focal.id and m not in leds]
        if others:
            continue
        led = leds[0]
        other_name = board.net_name(ends[1 - i])
        return RoleResult(
            rule="led_series_resistor",
            title="LED 限流电阻",
            summary=f"串在 {led.id} 与 {other_name} 之间，限制 LED 电流",
            evidence=[
                f"{board.focal.id} {board.value_text(board.focal)}："
                f"{board.net_name(ends[1 - i])} ↔ {board.net_name(nid)}（{led.id} 阳极）",
                f"限流对象：{led.id}（{board.value_text(led)}）",
            ],
            confidence="high",
            data={"function": "led_series_resistor", "led": led.id},
        )
    return None


def _usb_connector_data_lines(board, nets: list[str]) -> list[str]:
    """USB 连接器上的数据线网络名（D+/D-/USB+/USB-）。"""
    out = []
    for name in nets:
        up = name.strip().upper()
        if up in ("D+", "D-", "USB+", "USB-"):
            out.append(name)
    return out


def rule_usb_type_c(board, pin_nets) -> RoleResult | None:
    """USB Type-C 接口：CC 线上带 Rd 下拉＝受电端（UFP），从外部取电。"""
    if board.focal.device_kind.value != "Connector":
        return None
    joined = " ".join(
        t for t in (board.focal.footprint, board.focal.raw_value, board.focal.part_number) if t
    )
    ends = _ends(board, pin_nets)
    cc_nets = [nid for nid in ends if _CC_NET_RE.match(board.net_name(nid).strip())]
    is_typec = bool(_TYPEC_RE.search(joined)) or bool(cc_nets)
    if not is_typec:
        return None
    if not _TYPEC_RE.search(joined) and not cc_nets:
        return None
    # Rd（5.1kΩ）下拉＝UFP 受电端：接口从外部取电
    sink = False
    for nid in cc_nets:
        for m in board.members(nid):
            if m.device_kind.value != "Resistor":
                continue
            other = next(
                (board.net_of_pin(p.id) for p in m.pins if board.net_of_pin(p.id) != nid), None
            )
            resistance = _resistance_ohms(m)
            if (
                other is not None
                and _is_ground(board, other)
                and resistance is not None
                and abs(resistance - 5100.0) <= 5100.0 * 0.05
            ):
                sink = True
    title = "USB Type-C 输入接口" if sink else "USB Type-C 接口"
    summary = (
        "Type-C 受电端：从外部取 5V 供电，CC 线下拉 5.1kΩ 声明 UFP"
        if sink
        else "Type-C 连接器：CC/电源/数据各占一组引脚"
    )
    return RoleResult(
        rule="usb_type_c",
        title=title,
        summary=summary,
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}：{title}",
            "CC 线：" + (", ".join(board.net_name(n) for n in cc_nets) or "无"),
        ],
        confidence="high" if cc_nets else "medium",
        data={"function": "usb_type_c", "sink": sink},
    )


def rule_usb_power_port(board, pin_nets) -> RoleResult | None:
    """USB 供电口：只引出 5V 与 GND，不带数据线（纯取电/供电座）。"""
    if board.focal.device_kind.value != "Connector":
        return None
    joined = " ".join(
        t for t in (board.focal.footprint, board.focal.raw_value, board.focal.part_number) if t
    )
    if "USB" not in joined.upper():
        return None
    ends = _ends(board, pin_nets)
    if not ends:
        return None

    def power_kind(nid: str) -> str | None:
        if _is_rail(board, nid):
            return "power"
        if _is_ground(board, nid):
            return "gnd"
        # 允许电源/地经明确的 0Ω 电感位引入；不能把未知网络直接当电源。
        for member in board.members(nid):
            if (
                member.id == board.focal.id
                or member.device_kind.value not in ("Inductor", "Resistor")
            ):
                continue
            texts = [str(t).strip() for t in (member.raw_value, member.part_number) if t]
            if not any(_RESISTOR_PACKAGE_RE.match(t) for t in texts):
                continue
            pins = member.pins
            pins_by_number = {pin.number: pin for pin in pins}
            ordered_numbers = sorted(
                pins_by_number,
                key=lambda number: (0, int(number)) if number.isdigit() else (1, number),
            )
            for pin in pins:
                if board.net_of_pin(pin.id) != nid:
                    continue
                if len(pins) == 2:
                    other_number = next(
                        number for number in pins_by_number if number != pin.number
                    )
                elif len(pins) % 2 == 0:
                    half = len(pins) // 2
                    index = ordered_numbers.index(pin.number)
                    other_number = ordered_numbers[
                        index + half if index < half else index - half
                    ]
                else:
                    continue
                other = board.net_of_pin(pins_by_number[other_number].id)
                if _is_rail(board, other):
                    return "power"
                if _is_ground(board, other):
                    return "gnd"
        return None

    kinds = {power_kind(nid) for nid in ends}
    if "power" not in kinds or "gnd" not in kinds:
        return None
    for nid in ends:
        if power_kind(nid) is not None:
            continue
        # 仅接受完全未外接的差分对（至少两个连接器脚）；其他未知网络
        # 以及只有一个脚的自动网络都不能证明“无数据”。
        focal_pins = [p for p in board.focal.pins if board.net_of_pin(p.id) == nid]
        if len(focal_pins) < 2 or any(m.id != board.focal.id for m in board.members(nid)):
            return None
    nets = [board.net_name(n) for n in ends]
    return RoleResult(
        rule="usb_power_port",
        title="USB 供电口（只出 5V/GND）",
        summary="只引出 5V 与 GND 的 USB 插座：取电或对外供电，不走数据线",
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}：USB 供电口",
            "引出网络：" + ", ".join(nets),
            "数据线：",
        ],
        confidence="medium",
        data={"function": "usb_power_port"},
    )


def rule_swd_debug_connector(board, pin_nets) -> RoleResult | None:
    """SWD 调试下载接口：接插件上引出 SWDIO/SWCLK/SWDAT 一组调试信号。"""
    if board.focal.device_kind.value != "Connector":
        return None
    nets = [board.net_name(n) for n in _ends(board, pin_nets)]
    swd = [n for n in nets if _SWD_SIGNAL_RE.match(n.strip().upper())]
    if len(swd) < 2:
        return None
    return RoleResult(
        rule="swd_debug_connector",
        title="SWD 调试下载接口",
        summary="接插件引出 SWD 调试信号（SWDIO/SWCLK/SWDAT），供下载器与调试器接入",
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}：SWD 调试口",
            "调试网络：" + ", ".join(swd),
        ],
        confidence="high",
        data={"function": "swd_debug_connector"},
    )


def rule_cc_series_switch(board, pin_nets) -> RoleResult | None:
    """CC 线串接开关：NMOS 串在 USB-C 的 CC 线与 CC 控制器之间。"""
    if board.focal.device_kind.value != "Mosfet":
        return None
    # 源/漏（不是栅极）的网络
    sd_nets: list[str | None] = []
    for p in board.focal.pins:
        pname = (p.name or "").upper()
        if pname in ("G", "GATE"):
            continue
        sd_nets.append(board.net_of_pin(p.id))
    sd_nets = [n for n in sd_nets if n is not None]
    if len(sd_nets) < 2:
        return None
    cc_side = next((n for n in sd_nets if _CC_NET_RE.match(board.net_name(n).strip())), None)
    ctrl_side = next((n for n in sd_nets if n != cc_side), None)
    if cc_side is None or ctrl_side is None:
        return None
    # CC 线一侧必须真的接到 Type-C 连接器
    connectors = [
        m for m in board.members(cc_side) if m.device_kind.value == "Connector"
    ]
    if not connectors:
        return None
    # 另一侧通过电阻接到 CC 控制器脚（CC1ID / CC2IO …）
    ctrl_nets: list[str] = []
    for m in board.members(ctrl_side):
        if m.device_kind.value != "Resistor":
            continue
        for p in m.pins:
            other = board.net_of_pin(p.id)
            if other in (None, ctrl_side):
                continue
            name = board.net_name(other)
            if _CC_CTRL_NET_RE.match(name.strip()):
                ctrl_nets.append(name)
    if not ctrl_nets:
        return None
    return RoleResult(
        rule="cc_series_switch",
        title="CC 线串接开关",
        summary="NMOS 串在 USB-C 的 CC 线与 CC 控制器之间：控制 CC 通道的通断/隔离",
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}：CC 线串接开关",
            f"连接器侧 CC：{board.net_name(cc_side)}"
            f"（{', '.join(c.id for c in connectors)}）",
            f"控制器侧：{board.net_name(ctrl_side)} → {', '.join(sorted(set(ctrl_nets)))}",
        ],
        confidence="high",
        data={"function": "cc_series_switch"},
    )


def _wireless_coil_evidence(board, comp: Component) -> list[str] | None:
    """无线充发射线圈的判定与证据；不是发射线圈则返回 None。

    串联回路：桥臂 A → 谐振电容组 → 线圈 → 桥臂 B（或反过来）。电容组已被
    resonant-commutation-cap 模板认成谐振电容、线圈两端都接开关桥臂、且
    器件值不是电感量（"Coil" 这类写法）——三者齐备才断言，避免把 LLC /
    谐振变换器的普通输出电感也喊成发射线圈。
    """
    if comp.device_kind.value != "Inductor":
        return None
    # 线圈位的值不是电感量（无数字，如 "Coil"）——这是发射线圈的典型写法
    value_text = (board.value_text(comp) or "").strip()
    if value_text and re.search(r"\d", value_text):
        return None
    ends = _ends(board, [board.net_of_pin(p.id) for p in comp.pins])
    if len(ends) != 2:
        return None
    resonant_caps = {
        cid
        for blk in board.ir.blocks
        if blk.id.startswith("resonant-commutation-cap")
        for cid in blk.component_ids
    }
    if not resonant_caps:
        return None

    def _resonant_on(nid: str | None) -> list:
        return [m for m in board.members(nid) if m.id in resonant_caps]

    caps_end = next((n for n in ends if _resonant_on(n)), None)
    if caps_end is None:
        return None
    other = next(n for n in ends if n != caps_end)

    def _bridge_leg(nid: str | None) -> bool:
        return any(
            m.device_kind.value in ("Mosfet", "IC")
            for m in board.members(nid)
            if m.id != comp.id
        )

    # 电容组另一端接到另一条桥臂：串联回路确实跨在两桥臂之间
    caps_other_nets: set[str] = set()
    for m in _resonant_on(caps_end):
        caps_other_nets |= {
            board.net_of_pin(p.id) for p in m.pins
        } - {caps_end}
    if not (_bridge_leg(other) and any(_bridge_leg(n) for n in caps_other_nets)):
        return None
    return [
        f"{comp.id} {board.value_text(comp)}："
        f"{board.net_name(ends[0])} ↔ {board.net_name(ends[1])}",
        "谐振电容组：" + ", ".join(m.id for m in _resonant_on(caps_end)),
    ]


def rule_wireless_charge_coil(board, pin_nets) -> RoleResult | None:
    """无线充电发射线圈：串在功率桥两桥臂之间的谐振线圈。

    判定与证据见 :func:`_wireless_coil_evidence`（板级扫描复用同一套判据）。
    """
    evidence = _wireless_coil_evidence(board, board.focal)
    if evidence is None:
        return None
    return RoleResult(
        rule="wireless_charge_coil",
        title="无线充发射线圈",
        summary=(
            "与谐振电容组成串联回路跨接在功率桥两桥臂之间："
            "桥臂开关激励 LC 串联回路向外发射能量"
        ),
        evidence=evidence,
        confidence="medium",
        data={"function": "wireless_charge_coil"},
    )


#: 原始输入轨词表（未稳压的适配器/USB 输入），区别于 5V/3V3 等稳压输出轨。
_INPUT_RAIL_NAME_RE = re.compile(
    r"^(DCIN|VIN|VBUS|VBAT|VSYS|VPOWER|PWR_IN)([_\d].*)?$", re.IGNORECASE
)


def rule_usb_input_filter(board, pin_nets) -> RoleResult | None:
    """USB 输入 π 滤波位：0R/磁珠横跨"USB 连接器侧 ↔ 原始输入轨"、带接地脚。

    π 滤波口的串感位常贴 0Ω/磁珠：VBUS 进来先经它再进 DCIN，接地脚挂
    共模/差模滤波电容。多引脚（≥3，含地脚）+ 一端是 USB 连接器 +
    另一端是原始输入轨词表，三者齐备才认；5V/3V3 稳压输出侧的 0R 跳线
    （如送排针的 L3）不在此列，仍由 zero_ohm_jumper 表述。
    """
    if board.focal.device_kind.value != "Inductor":
        return None
    if len(board.focal.pins) < 3:
        return None
    nets = list(dict.fromkeys(
        n for n in (board.net_of_pin(p.id) for p in board.focal.pins) if n is not None
    ))
    if not any(_is_ground(board, n) for n in nets):
        return None

    def _usb_connector_on(nid: str) -> bool:
        return any(
            m.device_kind.value == "Connector"
            and re.search(r"USB|TYPE.?C", board.value_text(m) or "", re.IGNORECASE)
            for m in board.members(nid)
        )

    usb_end = next((n for n in nets if _usb_connector_on(n)), None)
    if usb_end is None:
        return None
    rail_end = next(
        (
            n for n in nets
            if n != usb_end and _INPUT_RAIL_NAME_RE.match(board.net_name(n).strip())
        ),
        None,
    )
    if rail_end is None:
        return None
    return RoleResult(
        rule="usb_input_filter",
        title="USB VBUS 输入滤波位",
        summary=(
            "USB 输入 π 网络的串感位：VBUS 经它进输入轨，"
            "接地脚挂滤波电容（当前贴 0Ω 时是直通位）"
        ),
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}："
            f"{board.net_name(usb_end)}（USB 连接器侧） ↔ {board.net_name(rail_end)}",
            "接地脚网络：" + ", ".join(
                board.net_name(n) for n in nets if _is_ground(board, n)
            ),
        ],
        confidence="medium",
        data={"function": "usb_input_filter"},
    )


def rule_zero_ohm_jumper(board, pin_nets) -> RoleResult | None:
    """0R 跳线：电感位上装的其实是 0Ω/电阻封装件，用来连通两段网络。"""
    if board.focal.device_kind.value != "Inductor":
        return None
    texts = [str(t).strip() for t in (board.focal.raw_value, board.focal.part_number) if t]
    if not any(_RESISTOR_PACKAGE_RE.match(t) for t in texts):
        return None
    ends = _ends(board, pin_nets)
    if not ends:
        return None
    return RoleResult(
        rule="zero_ohm_jumper",
        title="0R 跳线网络",
        summary="电感位上装的是 0Ω/电阻封装件：连通两段网络，便于调试割断",
        evidence=[
            f"{board.focal.id} {board.value_text(board.focal)}：0R 跳线（位号挂在电感位）",
            "连接网络：" + ", ".join(board.net_name(n) for n in ends),
        ],
        confidence="medium",
        data={"function": "zero_ohm_jumper"},
    )


def rule_touch_electrode_resistor(board, pin_nets) -> RoleResult | None:
    """触摸电极串阻：串在触摸电极（探针位）与触摸 IC 感应脚之间的电阻。"""
    if board.focal.device_kind.value != "Resistor":
        return None
    ends = _ends(board, pin_nets)
    if len(ends) != 2:
        return None
    for i, nid in enumerate(ends):
        members = board.members(nid)
        probes = [m for m in members if _is_probe_point(m)]
        if not probes:
            continue
        others = [m for m in members if m.id != board.focal.id and m not in probes]
        if others:
            continue
        # 另一端必须落到 IC 的感应脚（TCH / TOUCH / 电容式按键）
        ic_end = ends[1 - i]
        touch_pins = [
            f"{ic.id}.{pn}"
            for ic, pn, pname in _ic_pins_on(board, ic_end)
            if (pname or "").upper().startswith(("TCH", "TOUCH", "KEY", "ELECTRODE"))
        ]
        if not touch_pins:
            continue
        probe = probes[0]
        return RoleResult(
            rule="touch_electrode_resistor",
            title="触摸电极串阻",
            summary=f"串在触摸电极 {probe.id} 与触摸 IC（{', '.join(touch_pins)}）之间，"
            "调整感应灵敏度并隔离电极",
            evidence=[
                f"{board.focal.id} {board.value_text(board.focal)}："
                f"{board.net_name(nid)}（电极 {probe.id}）↔ {board.net_name(ic_end)}",
                "触摸 IC 感应脚：" + ", ".join(touch_pins),
            ],
            confidence="high",
            data={"function": "touch_electrode_resistor", "electrode": probe.id},
        )
    return None


# ---------------------------------------------------------------------------
# 规则表
# ---------------------------------------------------------------------------

#: 属性规则（器件"是什么"）——先于模板命中，属性证据比模板拓扑更硬。
IDENTITY_RULES = [
    rule_crystal,
    rule_fuse,
    rule_display_module,
    rule_thermocouple_terminal,
    rule_speaker,
    rule_battery,
    rule_switch,
    rule_test_point,
    # 证据足够具体的几条：必须在模板命中之前判定，否则宽泛模板会抢走
    # （例如"基极限流电阻"会被 pull-up 模板误命中成"上拉电阻"）。
    rule_bjt_base_resistor,
    rule_sense_divider_arm,
    rule_feedback_divider_arm,
    rule_rail_resistor,
    rule_charger_prog_resistor,
]

#: 器件角色规则（这个器件在板上"干什么"）——**在具体功能模板块之后**判定。
#:
#: 为什么排在模板块之后：这些规则给出的是**通用角色**（"NPN 三极管"、
#: "接插件"），而 primitives 模板块给出的是**电路身份**（"LED 驱动低边开关"）。
#: 元件已经是某个功能块的一部分时，块的身份更准确——例如 Q6（SS8050）
#: 既是"NPN 三极管"，更是"LED 驱动低边开关"，后者才是它在这块板上的角色。
#:
#: 上面 IDENTITY_RULES 里的"基极限流电阻 / 分压臂 / 轨间电阻 / 充电编程电阻"
#: 不在此列：它们必须抢在宽泛模板（pull-up / pull-down）前面，故留在模板之前。
DEVICE_ROLE_RULES = [
    rule_transistor,
    rule_usb_type_c,
    rule_usb_power_port,
    rule_swd_debug_connector,
    rule_connector,
    rule_ic_function,
    rule_touch_key_ic,
    rule_led_indicator,
    rule_cc_series_switch,
    rule_wireless_charge_coil,
    rule_usb_input_filter,
    rule_zero_ohm_jumper,
    rule_led_series_resistor,
    rule_touch_electrode_resistor,
]

#: 拓扑规则（元件"干什么"）——在模板命中之后、live match 之前兜底。
TOPOLOGY_RULES = [
    rule_crystal_load_cap,
    rule_crystal_feedback_resistor,
    rule_power_drop_diode,
    rule_coupling_cap,
    # 分压下臂必须在"未连接"之前：网表漏导出下臂时，两条规则都能说，
    # 但"疑似下臂"比"未连接"多一条可用旁证（兄弟上臂已被识别）。
    rule_sense_divider_lower_arm,
    rule_unconnected,
]

#: 兜底规则——模板与 live match 都没命中时才用（证据弱，但好过"未命中"）。
LAST_RULES = [
    rule_decoupling_cap,
    rule_filter_cap,
    rule_series_resistor,
]


def apply_identity_rules(board, pin_nets) -> RoleResult | None:
    """按顺序跑属性规则，首个命中即返回。"""
    for rule in IDENTITY_RULES:
        result = rule(board, pin_nets)
        if result is not None:
            return result
    return None


def apply_device_role_rules(board, pin_nets) -> RoleResult | None:
    """按顺序跑器件角色规则，首个命中即返回。

    在具体功能模板块之后调用（见 :data:`DEVICE_ROLE_RULES` 的说明）。
    """
    for rule in DEVICE_ROLE_RULES:
        result = rule(board, pin_nets)
        if result is not None:
            return result
    return None


def apply_topology_rules(board, pin_nets) -> RoleResult | None:
    """按顺序跑拓扑规则，首个命中即返回。"""
    for rule in TOPOLOGY_RULES:
        result = rule(board, pin_nets)
        if result is not None:
            return result
    return None


def apply_last_rules(board, pin_nets) -> RoleResult | None:
    """兜底规则（模板与 live match 都没命中时用）。"""
    for rule in LAST_RULES:
        result = rule(board, pin_nets)
        if result is not None:
            return result
    return None


__all__ = [
    "IDENTITY_RULES",
    "TOPOLOGY_RULES",
    "apply_identity_rules",
    "apply_topology_rules",
    "rule_battery",
    "rule_bjt_base_resistor",
    "rule_connector",
    "rule_coupling_cap",
    "rule_crystal",
    "rule_crystal_feedback_resistor",
    "rule_crystal_load_cap",
    "rule_decoupling_cap",
    "rule_display_module",
    "rule_feedback_divider_arm",
    "rule_filter_cap",
    "rule_fuse",
    "rule_ic_function",
    "rule_power_drop_diode",
    "rule_rail_resistor",
    "rule_sense_divider_arm",
    "rule_sense_divider_lower_arm",
    "rule_series_resistor",
    "rule_speaker",
    "rule_switch",
    "rule_thermocouple_terminal",
    "rule_transistor",
    "rule_unconnected",
]
