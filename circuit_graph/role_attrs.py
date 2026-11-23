"""元件属性识别 —— 只看器件**自身属性**（封装 / 型号 / 值 / 位号前缀）判断"这是什么器件"。

设计约束：

* 全部是通用电子工程知识（频率值 = 晶振、``0.5A`` 贴片规格 = 保险丝、``8050`` = NPN
  三极管、``SEG/COM`` 屏 = LCD 模组 …），**不含任何板卡位号特例**。
* 只回答"是什么"，不回答"干什么"——功能定性由 :mod:`circuit_graph.role_rules`
  结合拓扑完成。
* 识别不到就返回 ``None``：宁可未命中，不要乱定性。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from circuit_graph.ir import Component

# ---------------------------------------------------------------------------
# 正则
# ---------------------------------------------------------------------------

# 频率值：32768HZ / 12MHz / 11.7952MHZ / 32.768 kHz
_FREQ_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(GHZ|MHZ|KHZ|HZ)\s*$", re.IGNORECASE)

# 熔断器规格：S1206-0.5A / 1206 0.5A / FUSE-1A
_FUSE_RATING_RE = re.compile(
    r"^\s*(?:S\s*)?\d{4}\s*[-_/]?\s*(\d+(?:\.\d+)?)\s*A\b", re.IGNORECASE
)
_FUSE_WORD_RE = re.compile(r"FUSE|MELT|BLOW|熔断", re.IGNORECASE)

# 晶振封装：CRYSTAL32 / XTAL / X-TAL / OSC-*
_CRYSTAL_FP_RE = re.compile(
    r"CRYSTAL|XTAL|X[-_]?TAL|CERAMIC[-_]?RES|\bOSC\b|石英", re.IGNORECASE
)

# 三极管型号族（NPN / PNP）—— 只收录通用器件事实可确认的型号；
# 拿不准的型号（KSC184/KSC285/KSC301、MMBT3401、MMBT4930、8551、2N3901、
# MMBT5411 …）一律不收录：宁可 unknown，不要乱定性。裸 3904/3906 也不收——
# 会与 4 位 SMD 电阻阻值代码（3904 = 3.9MΩ）相撞。
# 两张表必须互斥（tests/test_role_attrs.py 有守护测试）。
_NPN_PARTS = frozenset({
    "8050", "S8050", "SS8050",
    "9011", "S9011", "9013", "S9013", "9014", "S9014", "9016", "S9016",
    "9018", "S9018",
    "MMBT3903", "MMBT3904", "MMBT5551",
    "2N2221", "2N2222", "2N3903", "2N3904", "2N4401",
    "BC547", "BC337", "BC817", "BC847",
})
_PNP_PARTS = frozenset({
    "8550", "S8550", "SS8550",
    "9012", "S9012", "9015", "S9015",
    "MMBT3905", "MMBT3906", "MMBT2907", "MMBT5401",
    "2N2905", "2N2906", "2N2907", "2N3905", "2N3906",
    "BC327", "BC557", "BC857",
})
_NPN_PART_RE = re.compile(
    r"^(?:%s)$" % "|".join(sorted(_NPN_PARTS)),
    re.IGNORECASE,
)
_PNP_PART_RE = re.compile(
    r"^(?:%s)$" % "|".join(sorted(_PNP_PARTS)),
    re.IGNORECASE,
)

# 显示模组：LCD 屏 / SEG+COM 屏
_DISPLAY_RE = re.compile(
    r"LCD|SEG\d*\s*[-_]?\s*\d*\s*SEG|COM\d+\s*[-_]?\s*\d+\s*COM|液晶|显示屏",
    re.IGNORECASE,
)

# 热电偶端子
_THERMOCOUPLE_RE = re.compile(
    r"THERMOCOUPL|THERMO\s*COUPLE|\bCOUPLE\b|热电偶", re.IGNORECASE
)

# 蜂鸣器 / 扬声器
_SPEAKER_RE = re.compile(r"BUZZER|SPEAKER|BUZZ\b|蜂鸣|喇叭", re.IGNORECASE)

# 电池
_BATTERY_RE = re.compile(r"BATTERY|BATT|CELL\s*\d|电池", re.IGNORECASE)

# 开关（DIP / 拨码 / 按键）
_SWITCH_RE = re.compile(
    r"SWITCHDIP|DIP\s*SWITCH|KEYSW|TACT\s*SW|SWITCH", re.IGNORECASE
)

# 电源/电池轨网络名
_RAIL_NET_RE = re.compile(
    r"^(?:VBUS|VBUS\d*|VBAT\d*|VBATT\d*|VBAT|VIN\d*|VCC\d*|VDD\d*|VDDA?"
    r"|BATT\d*|BATT|BAT\d*|LION|ACCU\d*|CELL\d*|PACK\d*|\+?\d+(?:\.\d+)?V\d*"
    r"|V\+\w*|PWR\w*|DCIN\w*)$",
    re.IGNORECASE,
)

_BATTERY_NET_RE = re.compile(
    r"^(?:VBAT\d*|VBATT\d*|BATT\d*|BATT|BAT\d*|LION|LIION|ACCU\d*|CELL\d*|PACK\d*)$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 器件类别
# ---------------------------------------------------------------------------

#: 属性类别常量
ATTR_CRYSTAL = "crystal"
ATTR_FUSE = "fuse"
ATTR_NPN = "npn"
ATTR_PNP = "pnp"
ATTR_DISPLAY = "display"
ATTR_THERMOCOUPLE = "thermocouple"
ATTR_SPEAKER = "speaker"
ATTR_BATTERY = "battery"
ATTR_SWITCH = "switch"


@dataclass(frozen=True)
class AttrFacts:
    """一个元件的属性判定结果。"""

    kind: str | None = None
    detail: str = ""
    value: float | None = None

    @property
    def known(self) -> bool:
        return self.kind is not None


def _texts(comp: Component) -> list[str]:
    """元件所有可用的属性文本（封装 / 型号 / 原值）。"""
    out: list[str] = []
    for t in (comp.footprint, comp.part_number, comp.raw_value):
        if t:
            out.append(str(t))
    return out


def crystal_frequency(comp: Component) -> float | None:
    """从属性里读出晶振频率（Hz）；读不到返回 None。

    例：``32768HZ`` → 32768.0，``12MHz`` → 12e6。
    """
    for text in _texts(comp):
        m = _FREQ_RE.match(text)
        if m:
            mult = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}[m.group(2).upper()]
            return float(m.group(1)) * mult
    return None


def fuse_rating(comp: Component) -> str | None:
    """从属性里读出熔断器额定电流（如 ``0.5A``）；读不到返回 None。"""
    for text in _texts(comp):
        m = _FUSE_RATING_RE.match(text)
        if m:
            return f"{m.group(1)}A"
    return None


def classify(comp: Component) -> AttrFacts:
    """判定元件的**属性类别**（是什么器件），不判定功能（干什么）。"""
    texts = _texts(comp)
    joined = " | ".join(texts)

    # 晶振：频率值 或 晶振封装。频率值是强证据。
    freq = crystal_frequency(comp)
    if freq is not None:
        return AttrFacts(ATTR_CRYSTAL, f"{freq:g}Hz", freq)
    if any(_CRYSTAL_FP_RE.search(t) for t in texts):
        return AttrFacts(ATTR_CRYSTAL, "晶振封装", None)

    # 熔断器：额定电流规格 或 FUSE 关键字
    rating = fuse_rating(comp)
    if rating is not None:
        return AttrFacts(ATTR_FUSE, rating, None)
    if _FUSE_WORD_RE.search(joined):
        return AttrFacts(ATTR_FUSE, "FUSE", None)

    # 三极管：型号族
    for text in texts:
        if _NPN_PART_RE.match(text.strip()):
            return AttrFacts(ATTR_NPN, text.strip(), None)
    for text in texts:
        if _PNP_PART_RE.match(text.strip()):
            return AttrFacts(ATTR_PNP, text.strip(), None)

    # 显示模组
    if any(_DISPLAY_RE.search(t) for t in texts):
        return AttrFacts(ATTR_DISPLAY, "", None)

    # 热电偶端子
    if any(_THERMOCOUPLE_RE.search(t) for t in texts):
        return AttrFacts(ATTR_THERMOCOUPLE, "", None)

    # 蜂鸣器 / 扬声器
    if any(_SPEAKER_RE.search(t) for t in texts):
        return AttrFacts(ATTR_SPEAKER, "", None)

    # 电池
    if any(_BATTERY_RE.search(t) for t in texts):
        return AttrFacts(ATTR_BATTERY, "", None)

    # 开关
    if any(_SWITCH_RE.search(t) for t in texts):
        return AttrFacts(ATTR_SWITCH, "", None)

    # 电池：属性无线索时用通用位号约定（BAT*/BATT*，与 U=IC、R=电阻 同级）
    if re.match(r"^BATT?[\d]*$", (comp.id or "").strip(), re.IGNORECASE):
        return AttrFacts(ATTR_BATTERY, "位号约定 BAT*", None)

    return AttrFacts()


def is_rail_net(name: str) -> bool:
    """网络名是否是电源/电池轨（+3.3V / VBUS / VBATT / LION …）。"""
    return bool(_RAIL_NET_RE.match(name.strip()))


def is_battery_net(name: str) -> bool:
    """网络名是否是电池轨（VBAT / LION / BATT / CELL …）。"""
    return bool(_BATTERY_NET_RE.match(name.strip()))


def is_crystal_net(name: str) -> bool:
    """网络名是否是晶振/时钟节点（XT1 / XTAL2 / OSC32K / 32K …）。"""
    return bool(
        re.match(r"^(?:XT\d*|XTAL\d*|XIN|XOUT|OSC\d*|OSCI|OSCO|CLK\d*|32K\d*)$",
                 name.strip(), re.IGNORECASE)
    )


def is_segment_net(name: str) -> bool:
    """网络名是否是 LCD 段/公共端（SEG0… / COM3…）。"""
    return bool(re.match(r"^(?:SEG|COM)\d*$", name.strip(), re.IGNORECASE))


def is_thermocouple_net(name: str) -> bool:
    """网络名是否是热电偶输入（T+ / T- / TC+ / TC-）。"""
    return bool(re.match(r"^(?:T|TC|TH)\s*[+-]\d?$", name.strip(), re.IGNORECASE))


__all__ = [
    "ATTR_BATTERY",
    "ATTR_CRYSTAL",
    "ATTR_DISPLAY",
    "ATTR_FUSE",
    "ATTR_NPN",
    "ATTR_PNP",
    "ATTR_SPEAKER",
    "ATTR_SWITCH",
    "ATTR_THERMOCOUPLE",
    "AttrFacts",
    "classify",
    "crystal_frequency",
    "fuse_rating",
    "is_battery_net",
    "is_crystal_net",
    "is_rail_net",
    "is_segment_net",
    "is_thermocouple_net",
]
