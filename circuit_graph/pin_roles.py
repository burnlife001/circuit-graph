"""Pin-role vocabulary bridging circuit pin names and primitive-template pin numbers.

匹配器（vf2_matcher.py）用这里的词表判断"模板期望的引脚角色"是否与
"电路里实际承载该网络的引脚"一致，从而避免仅凭连通性就把任意 IC 套上模板
（例如 JW5065 的 VIN 被误当成运放的 V+）。

两张表分别服务两种命名习惯：
- ``_CIRCUIT_PIN_ROLES``：KB（PinNameResolver）解析出的真实引脚名 → 角色。
- ``_TEMPLATE_PIN_ROLES``：primitives/*.json 里手写的逻辑引脚名 → 期望角色。

``supply``（运放/比较器类的 V+）与 ``dc_input``（降压芯片的 VIN）被显式区分成
两个不同角色——这是本词表存在的核心原因：没有这条界线，任何带电源输入的 IC
都会被误认成运放。
"""

from __future__ import annotations

# 角色 token -> 电路引脚名集合（KB 解析结果，大小写不敏感，见 __post_init__ 归一化）。
_CIRCUIT_PIN_ROLES: dict[str, set[str]] = {
    "supply": {"V+", "VCC", "VDD", "VPOS"},
    "ground": {"V-", "GND", "VSS", "VEE", "VNEG"},
    "noninv_in": {"+", "IN+", "IN_P", "NONINV", "AINP", "NINV"},
    "inv_in": {"-", "IN-", "IN_N", "INV", "AINN", "NEG"},
    "output": {"OUT", "OUTPUT", "O", "OC"},
    "switch": {"SW", "LX", "SW1", "SW2"},
    "feedback": {"FB", "VFB"},
    "enable": {"EN", "EN1"},
    "bootstrap": {"BST", "BS", "BST1"},
    "dc_input": {"VIN", "VIN1", "IN"},
    "nc": {"NC"},
    # --- 分立器件引脚 ---
    # 三极管（BJT）与 MOSFET 的引脚名在 KB 里是可区分的（B/E/C vs G/D/S），
    # 有了这层角色，模板里写的 "C"/"E"/"B" 才是**有约束力**的：8050 的三个脚
    # 在 KB 里叫 B/E/C，AO3400 叫 G/S/D，两者不会互相冒充。
    "base": {"B"},
    "collector": {"C"},
    "emitter": {"E"},
    "gate": {"G"},
    "drain": {"D"},
    "source": {"S"},
}

# 角色 token -> 模板里允许写的逻辑引脚名集合（primitives/*.json 里 pin.number）。
_TEMPLATE_PIN_ROLES: dict[str, set[str]] = {
    "supply": {"V+", "VCC", "VDD", "VPOS"},
    "ground": {"V-", "GND", "VSS", "VEE", "VNEG"},
    "noninv_in": {"+", "IN+", "IN_P", "NONINV", "AINP"},
    "inv_in": {"-", "IN-", "IN_N", "INV", "AINN"},
    "output": {"OUT", "OUTPUT", "O"},
    "switch": {"SW", "LX"},
    "feedback": {"FB", "VFB"},
    "enable": {"EN"},
    "bootstrap": {"BST", "BS"},
    "dc_input": {"VIN", "IN"},
    # 分立器件引脚（与电路侧同名）：模板写 "C"/"E"/"B" 表示 BJT 的
    # 集电极/发射极/基极，写 "D"/"S"/"G" 表示 MOSFET 的漏/源/栅。
    # 匹配器据此把模板引脚与 KB 解析出的真实引脚名对上，
    # 从而消掉"哪根脚是集电极"这类拓扑歧义。
    "base": {"B"},
    "collector": {"C"},
    "emitter": {"E"},
    "gate": {"G"},
    "drain": {"D"},
    "source": {"S"},
    # "nc" 模板侧没有定义（— in the table): 不建反查索引。
}


def _invert(table: dict[str, set[str]]) -> dict[str, str]:
    """角色表 -> 名称(大写) -> 角色 的反查表。"""
    lookup: dict[str, str] = {}
    for role, names in table.items():
        for name in names:
            lookup[name.upper()] = role
    return lookup


_CIRCUIT_NAME_TO_ROLE = _invert(_CIRCUIT_PIN_ROLES)
_TEMPLATE_NAME_TO_ROLE = _invert(_TEMPLATE_PIN_ROLES)


def role_of_circuit_pin(name: str | None) -> str | None:
    """把 KB 解析出的电路引脚名映射到角色 token。

    未知名字（KB 没解析出来，或词表里没有）返回 ``None``，表示"未标注"——
    调用方对此应当宽容放行，避免误杀多数没有引脚名的元件。
    """
    if not name:
        return None
    return _CIRCUIT_NAME_TO_ROLE.get(name.strip().upper())


def role_of_template_pin(number: str | None) -> str | None:
    """把模板里写的逻辑引脚名映射到期望角色。

    纯数字的物理引脚号（如 ``"3"``）返回 ``None``，表示该模板不按角色约束，
    走原有的"引脚号精确匹配"路径。
    """
    if not number:
        return None
    text = number.strip()
    if text.isdigit():
        return None
    return _TEMPLATE_NAME_TO_ROLE.get(text.upper())


def pin_names_resolved_ratio(pins) -> float:
    """``comp.pins`` 中有名字的比例；空列表返回 0.0。"""
    pins = list(pins)
    if not pins:
        return 0.0
    named = sum(1 for p in pins if getattr(p, "name", None))
    return named / len(pins)


def gate_active(
    pin_category_provider,
    comp,
    *,
    min_resolved_ratio: float = 0.6,
) -> bool:
    """角色约束是否对 ``comp`` 生效（见 A1 门控的三条）。

    1. 有 provider；
    2. ``provider(comp)`` 非空（元件在 KB 里且有 pinout —— 不在库返回 ``None``，
       在库但无 pinout 返回 ``{}``，两者都视为"不门控"）；
    3. ``comp.pins`` 里 >= ``min_resolved_ratio`` 的引脚有名字
       （KB pinout 基本解析完整，不是网络名回显等测试数据伪影）。
    """
    if pin_category_provider is None:
        return False
    try:
        cats = pin_category_provider(comp)
    except Exception:  # noqa: BLE001 —— provider 失败时不门控，保持宽松
        return False
    if not cats:
        return False
    return pin_names_resolved_ratio(comp.pins) >= min_resolved_ratio


def ic_role_subset_ok(template_component, circuit_comp) -> bool:
    """IC 级角色子集校验（A3）：电路 IC 的角色集合必须覆盖模板 IC 要求的角色集合。

    调用方必须先确认 :func:`gate_active`，本函数不重复判门控。
    ``required`` 为空（模板全用数字引脚号，不按角色约束）时直接放行。
    """
    required = {
        role_of_template_pin(tp.number) for tp in template_component.pins
    } - {None}
    if not required:
        return True
    have = {role_of_circuit_pin(p.name) for p in circuit_comp.pins} - {None}
    return not (required - have)


__all__ = [
    "role_of_circuit_pin",
    "role_of_template_pin",
    "pin_names_resolved_ratio",
    "gate_active",
    "ic_role_subset_ok",
]
