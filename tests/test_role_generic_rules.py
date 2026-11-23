"""通用定性规则的单元测试 —— 全部用**合成网表**覆盖拓扑。

这些用例刻意使用"非典型位号"（XTAL9 / Q7 / K1 …），证明规则认的是
**属性与邻接拓扑**，不是某块板上的位号，也不依赖具体 IC 型号。
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from circuit_graph.cli.app import _parse_and_enrich
from circuit_graph.role import infer_role


def _netlist(
    components: dict[str, tuple[str, str]],
    nets: dict[str, list[str]],
) -> str:
    """拼一份 Protel legacy .NET（位号 / 封装 / 值 + 网络块）。"""
    out: list[str] = []
    for ref, (footprint, value) in components.items():
        out.append(f"[\n{ref}\n{footprint}\n{value}\n\n\n\n]\n")
    for name, pins in nets.items():
        out.append("(\n" + name + "\n" + "\n".join(pins) + "\n)\n")
    return "\n".join(out)


@pytest.fixture
def parse(tmp_path):
    """把合成网表解析成 CircuitIR。"""

    def _do(components, nets):
        p = tmp_path / "synthetic.NET"
        p.write_text(_netlist(components, nets), encoding="utf-8")
        return _parse_and_enrich(p, use_cache=False)

    return _do


def _provider(_comp):
    return None


# ---------------------------------------------------------------------------
# 属性规则
# ---------------------------------------------------------------------------


def test_crystal_from_frequency_value(parse) -> None:
    """频率值 = 晶振（32768HZ / 12MHz 这类值是强证据）。"""
    ir = parse(
        {"XTAL9": ("CRYSTAL32", "32768HZ"), "U7": ("LQFP32", "C8051F320")},
        {
            "XT1": ["XTAL9-1", "U7-31"],
            "XT2": ["XTAL9-2", "U7-32"],
            "GND": ["XTAL9-3", "U7-3"],
        },
    )
    r = infer_role(ir, "XTAL9", _provider)
    assert r is not None
    assert r.rule == "crystal"
    assert "晶振" in r.title
    assert "32.768 kHz" in r.title
    assert any("U7" in e for e in r.evidence)


def test_fuse_from_rating_value(parse) -> None:
    """0.5A 贴片规格 = 保险丝（只凭值 → 置信度 medium）。"""
    ir = parse(
        {"K1": ("1206", "S1206-0.5A"), "J4": ("USBFOOT4", "PC-USB")},
        {"VBUS": ["K1-2", "J4-1"], "NetK1_1": ["K1-1"]},
    )
    r = infer_role(ir, "K1", _provider)
    assert r is not None
    assert r.rule == "fuse"
    assert "保险丝" in r.title
    assert "0.5A" in r.title
    assert r.confidence == "medium"


def test_display_module_from_seg_com_nets(parse) -> None:
    """SEG/COM 网络 → 外接显示模组。"""
    ir = parse(
        {"DS1": ("LCD_4COM-12SEG", "LCD_4COM-12SEG"), "U2": ("SSOP48", "HT1621B")},
        {
            "SEG0": ["DS1-5", "U2-8"],
            "SEG1": ["DS1-6", "U2-7"],
            "SEG2": ["DS1-7", "U2-6"],
            "COM0": ["DS1-4", "U2-21"],
            "COM1": ["DS1-3", "U2-22"],
        },
    )
    r = infer_role(ir, "DS1", _provider)
    assert r is not None
    assert r.rule == "display_module"
    assert "LCD" in r.title
    assert any("U2" in e for e in r.evidence)


def test_thermocouple_terminal_from_couple_footprint(parse) -> None:
    """热电偶端子：COUPLE 封装 + T+/T- 网络。"""
    ir = parse(
        {"TC1": ("COUPLE", "Couple"), "U3": ("SO-8", "MAX6675")},
        {"T+": ["TC1-3", "U3-3"], "GND": ["TC1-1", "U3-1"]},
    )
    r = infer_role(ir, "TC1", _provider)
    assert r is not None
    assert r.rule == "thermocouple_terminal"
    assert "热电偶" in r.title


def test_thermocouple_terminal_dedupes_same_net(parse) -> None:
    """两个引脚落在同一 T+ 网上：证据只报一次，不重复刷屏。"""
    ir = parse(
        {"TC2": ("COUPLE", "Couple"), "U3": ("SO-8", "MAX6675")},
        {"T+": ["TC2-3", "TC2-5", "U3-3"], "GND": ["TC2-1", "U3-1"]},
    )
    r = infer_role(ir, "TC2", _provider)
    assert r is not None
    assert r.rule == "thermocouple_terminal"
    assert sum(1 for e in r.evidence if "热电偶信号：T+" in e) == 1


def test_npn_transistor_and_be_bleeder_resistor(parse) -> None:
    """NPN 三极管 + 基极-发射极泄放电阻（按型号族与 SOT-23 引脚约定判定）。"""
    ir = parse(
        {"Q7": ("SOT-23", "8050"), "R20": ("0805", "10K")},
        {
            "BASE": ["Q7-1", "R20-1"],
            "GND": ["Q7-2"],
            "LOAD": ["Q7-3"],
        },
    )
    q = infer_role(ir, "Q7", _provider)
    assert q is not None
    assert q.rule == "transistor"
    assert "NPN" in q.title

    r = infer_role(ir, "R20", _provider)
    assert r is not None
    assert r.rule == "bjt_base_resistor"
    assert "基极" in r.title
    assert "Q7" in r.title


def test_pnp_transistor_be_bleeder_resistor(parse) -> None:
    """PNP：基极经电阻回到发射极 → 泄放电阻（保证 PNP 关断）。"""
    ir = parse(
        {"Q9": ("SOT-23", "8550"), "R21": ("0805", "10K")},
        {
            "BASE": ["Q9-1", "R21-1"],
            "VBAT": ["Q9-2", "R21-2"],
            "VOUT": ["Q9-3"],
        },
    )
    q = infer_role(ir, "Q9", _provider)
    assert q is not None
    assert q.rule == "transistor"
    assert "PNP" in q.title

    r = infer_role(ir, "R21", _provider)
    assert r is not None
    assert r.rule == "bjt_base_resistor"
    assert r.data["relation"] == "base_emitter"
    assert "泄放" in r.title


def test_base_driver_resistor_from_ic_output(parse) -> None:
    """基极限流电阻：基极经电阻接 IC 输出。"""
    ir = parse(
        {
            "Q11": ("SOT-23", "8050"),
            "R22": ("0805", "10K"),
            "U8": ("LQFP32", "C8051F320"),
        },
        {
            "BEEP": ["U8-18", "R22-1"],
            "BASE": ["Q11-1", "R22-2"],
            "GND": ["Q11-2", "U8-3"],
            "LOAD": ["Q11-3"],
        },
    )
    r = infer_role(ir, "R22", _provider)
    assert r is not None
    assert r.rule == "bjt_base_resistor"
    assert r.data["relation"] == "base_driver"
    assert "限流" in r.title


def test_rail_arm_resistor_on_collector_net_is_divider_not_base(parse) -> None:
    """PNP 高边开关：集电极网络本身是电源轨，轨上引出的采样电阻是分压上臂，
    不是"基极限流电阻"——三极管规则必须让位给分压规则。"""
    ir = parse(
        {
            "Q9": ("SOT-23", "8550"),
            "RT": ("0805", "100K 1%"),
            "RD": ("0805", "100K  1%"),  # 悬空同值电阻（疑似缺失的下臂）
            "U8": ("LQFP32", "C8051F320"),
        },
        {
            "LION": ["Q9-2"],
            "BASE": ["Q9-1"],
            "+4.2V": ["Q9-3", "RT-2"],
            "SENSE": ["RT-1", "U8-16"],
        },
    )
    r = infer_role(ir, "RT", _provider)
    assert r is not None
    assert r.rule == "sense_divider_arm"
    assert "分压" in r.title
    assert "+4.2V" in r.title
    # 悬空同值电阻如实报告为疑似缺失的下臂
    assert "RD" in r.title or any("RD" in e for e in r.evidence)


# ---------------------------------------------------------------------------
# IC 功能规则（按引脚落点拓扑，不认具体型号）
# ---------------------------------------------------------------------------


def test_ldo_from_two_rails_and_ground(parse) -> None:
    """两个电源脚 + 一个地脚 → 线性稳压器（电压高的进、低的出）。"""
    ir = parse(
        {"U10": ("SOT-89", "HT7333")},
        {"+4.2V": ["U10-2"], "+3.3V": ["U10-3"], "GND": ["U10-1"]},
    )
    r = infer_role(ir, "U10", _provider)
    assert r is not None
    assert r.rule == "ic_function"
    assert r.data["function"] == "ldo"
    assert "LDO" in r.title
    assert "稳压" in r.summary


def test_rtc_from_i2c_int_and_crystal(parse) -> None:
    """I2C + 中断 + 晶振 → RTC。"""
    ir = parse(
        {"U11": ("SO-8", "PCF8563"), "XTAL2": ("CRYSTAL32", "32768HZ")},
        {
            "SDA": ["U11-5"],
            "SCL": ["U11-6"],
            "INT_TIMER": ["U11-3"],
            "OSC1": ["XTAL2-1", "U11-1"],
            "OSC2": ["XTAL2-2", "U11-2"],
            "GND": ["XTAL2-3", "U11-4"],
        },
    )
    r = infer_role(ir, "U11", _provider)
    assert r is not None
    assert r.rule == "ic_function"
    assert r.data["function"] == "rtc"
    assert "RTC" in r.title


# test_spi_memory_from_mosi_miso_sck 已删除：它断言"MOSI+MISO+SCK 拓扑即可
# 定成 SPI 串行存储器/Flash"，把拓扑证据升级成了器件身份——SPI ADC/传感器
# 走同样的信号线也会被冒称 Flash。修正后的行为见下方
# test_spi_bus_without_kb_category_is_peripheral_not_flash（无 KB 类别 → SPI
# 外设）与 test_kb_category_proves_spi_memory（KB 类别证实 → 存储器）。


def test_battery_charger_from_battery_rail_and_input(parse) -> None:
    """电池脚 + 输入电源脚 + 地脚 → 锂电池充电 IC。"""
    ir = parse(
        {"U13": ("TSOT-23", "LTC4054"), "R23": ("0805", "2.7K 1%")},
        {
            "LION": ["U13-3"],
            "VBUS": ["U13-4"],
            "GND": ["U13-2"],
            "CHG": ["U13-1"],
            "NetU13_5": ["U13-5", "R23-1"],
            "GND2": ["R23-2"],
        },
    )
    r = infer_role(ir, "U13", _provider)
    assert r is not None
    assert r.rule == "ic_function"
    assert r.data["function"] == "battery_charger"
    assert "充电" in r.title
    # 电池轨（LION）不能同时被认成"输入电源"——输入脚必须是另一条轨。
    assert any("电池脚 pin3 → LION" in e for e in r.evidence)
    assert any("输入脚 pin4 → VBUS" in e for e in r.evidence)
    assert "把 VBUS 的输入电源经内部线性稳压给 LION 充电" in r.summary

    prog = infer_role(ir, "R23", _provider)
    assert prog is not None
    assert prog.rule == "charger_prog_resistor"
    assert "充电电流" in prog.title


def test_mcu_from_many_pins_and_crystal(parse) -> None:
    """引脚多 + 接晶振 → 主控 MCU。"""
    nets = {
        "XT1": ["U14-11", "XTAL3-1"],
        "XT2": ["U14-12", "XTAL3-2"],
        "GND": ["U14-3", "XTAL3-3"],
        "P10": ["U14-1"],
        "P11": ["U14-2"],
        "P12": ["U14-4"],
        "P13": ["U14-5"],
        "P14": ["U14-6"],
        "P15": ["U14-7"],
        "P16": ["U14-8"],
        "P17": ["U14-9"],
        "P18": ["U14-10"],
    }
    ir = parse(
        {"U14": ("LQFP32", "C8051F320"), "XTAL3": ("CRYSTAL32", "32768HZ")},
        nets,
    )
    r = infer_role(ir, "U14", _provider)
    assert r is not None
    assert r.rule == "ic_function"
    assert r.data["function"] == "mcu"
    assert "MCU" in r.title


# ---------------------------------------------------------------------------
# 拓扑规则
# ---------------------------------------------------------------------------


def test_crystal_load_cap_from_crystal_net(parse) -> None:
    """接在晶振引脚网络上的电容 → 晶振负载电容。"""
    ir = parse(
        {"XTAL4": ("CRYSTAL32", "32768HZ"), "C30": ("0805", "22P")},
        {"XT1": ["XTAL4-1", "C30-1"], "GND": ["XTAL4-3", "C30-2"]},
    )
    r = infer_role(ir, "C30", _provider)
    assert r is not None
    assert r.rule == "crystal_load_cap"
    assert "晶振" in r.title
    assert any("XTAL4" in e for e in r.evidence)


def test_crystal_feedback_resistor(parse) -> None:
    """并联在晶体两端的电阻 → 晶振反馈电阻。"""
    ir = parse(
        {"XTAL5": ("CRYSTAL32", "32768HZ"), "R24": ("0805", "10M")},
        {
            "XT1": ["XTAL5-1", "R24-1"],
            "XT2": ["XTAL5-2", "R24-2"],
            "GND": ["XTAL5-3"],
        },
    )
    r = infer_role(ir, "R24", _provider)
    assert r is not None
    assert r.rule == "crystal_feedback_resistor"
    assert "晶振" in r.title


def test_power_drop_diode_feeding_downstream_ic(parse) -> None:
    """电池轨 → 二极管 → 下游 IC 供电 → 降压供电二极管。"""
    ir = parse(
        {"D30": ("SMD_4148", "1N4148"), "U15": ("SO-8", "PCF8563")},
        {"LION": ["D30-1"], "V+8563": ["D30-2", "U15-8"]},
    )
    r = infer_role(ir, "D30", _provider)
    assert r is not None
    assert r.rule == "power_drop_diode"
    assert "降压" in r.title
    assert "U15" in r.title


def test_decoupling_cap_on_rail(parse) -> None:
    """电源轨 ↔ 地 的电容 → 去耦电容。"""
    ir = parse(
        {"C31": ("0805", "104")},
        {"+3.3V": ["C31-1"], "GND": ["C31-2"]},
    )
    r = infer_role(ir, "C31", _provider)
    assert r is not None
    # 模板命中（dc-decoupling-cap）或手写规则命中都算对，答案口径一致。
    assert r.rule in ("decoupling", "primitive:dc-decoupling-cap-c31")
    assert "去耦" in r.title


def test_series_resistor_in_signal_path(parse) -> None:
    """两端都接 IC 的电阻 → 串联电阻（兜底规则）。"""
    ir = parse(
        {"R25": ("0805", "330"), "U16": ("LQFP32", "C8051F320")},
        {"RXD": ["U16-29", "R25-1"], "NetJ3_2": ["R25-2"]},
    )
    r = infer_role(ir, "R25", _provider)
    assert r is not None
    assert r.rule == "series_signal"
    assert "串联" in r.title


@pytest.mark.parametrize("value", ["4R7", "1R0", "R1"])
def test_inductance_value_is_not_zero_ohm_jumper(parse, value: str) -> None:
    ir = parse({"L1": ("0805", value)}, {"A": ["L1-1"], "B": ["L1-2"]})
    result = infer_role(ir, "L1", _provider)
    assert result is not None
    assert result.rule != "zero_ohm_jumper"


def test_unconnected_component_is_reported(parse) -> None:
    """网表里没有任何连接的元件 → 如实说没连上，不编功能。"""
    ir = parse({"R26": ("0805", "10K")}, {})
    r = infer_role(ir, "R26", _provider)
    assert r is not None
    assert r.rule == "unconnected"
    assert r.confidence == "low"


def test_usb_power_port_requires_only_power_and_ground(parse) -> None:
    positive = parse(
        {"USB2": ("USB_A", "USB-A")},
        {"VBUS": ["USB2-1"], "GND": ["USB2-4"]},
    )
    assert infer_role(positive, "USB2", _provider).rule == "usb_power_port"

    for extra_net in ("DP", "NetU2_1"):
        ir = parse(
            {"USB2": ("USB_A", "USB-A")},
            {"VBUS": ["USB2-1"], "GND": ["USB2-4"], extra_net: ["USB2-2"]},
        )
        result = infer_role(ir, "USB2", _provider)
        assert result is not None
        assert result.rule != "usb_power_port", extra_net


def test_usb_power_port_pairs_four_pin_jumper_by_pin_number(parse) -> None:
    """四脚 0R 的 1↔3、2↔4 配对不受 pins 列表顺序影响。"""
    ir = parse(
        {"L3": ("1206", "R-1206"), "USB2": ("USB_4PIN_DIP", "USB_4PIN_DIP")},
        {
            "VBUS": ["L3-3"],
            "GND": ["L3-4"],
            "NetL3_1": ["L3-1", "USB2-1"],
            "NetL3_2": ["L3-2", "USB2-4"],
            "NetUSB2_2": ["USB2-2", "USB2-3"],
        },
    )
    l3 = next(comp for comp in ir.components if comp.id == "L3")
    by_number = {pin.number: pin for pin in l3.pins}
    reordered_l3 = replace(l3, pins=[by_number[number] for number in ("1", "3", "2", "4")])
    ir = replace(
        ir,
        components=[reordered_l3 if comp.id == "L3" else comp for comp in ir.components],
    )
    result = infer_role(ir, "USB2", _provider)
    assert result is not None
    assert result.rule == "usb_power_port"


def test_type_c_sink_requires_5k1_rd(parse) -> None:
    components = {"USB1": ("TYPE_C", "TYPE_C"), "R1": ("0603", "5.1K")}
    nets = {
        "CC1": ["USB1-1", "R1-1"],
        "GND": ["USB1-2", "R1-2"],
        "VBUS": ["USB1-3"],
    }
    result = infer_role(parse(components, nets), "USB1", _provider)
    assert result is not None
    assert result.data["sink"] is True

    components["R1"] = ("0603", "10K")
    result = infer_role(parse(components, nets), "USB1", _provider)
    assert result is not None
    assert result.data["sink"] is False


# ---------------------------------------------------------------------------
# SPI 总线：拓扑只能证明"SPI 外设"；存储器要靠 KB 器件类别证实
# ---------------------------------------------------------------------------


def _spi_bus_netlist(parse, part_number: str):
    """MOSI + MISO + SCK + CS 的 SPI 总线接法（器件型号参数化）。"""
    return parse(
        {"U12": ("SO-8-W", part_number)},
        {
            "MOSI": ["U12-1"],
            "MISO": ["U12-8"],
            "SCK": ["U12-2"],
            "CS_AT45": ["U12-4"],
            "+3.3V": ["U12-5"],
            "GND": ["U12-7"],
        },
    )


def test_spi_bus_without_kb_category_is_peripheral_not_flash(parse) -> None:
    """MOSI/MISO/SCK 只能证明"SPI 外设 IC"，不能证明存储器。

    修正原断言（test_spi_memory_from_mosi_miso_sck 曾断言拓扑即 Flash）：
    SPI 总线接法在 SPI ADC / 传感器 / RTC 上同样成立，拓扑本身证明不了
    "存储器"，更证明不了"存固件"。没有 KB 明确器件类别时必须降级为
    "SPI 外设 IC"并给中等置信度——即便型号名看着像 Flash 也不许硬编码猜。
    """
    ir = _spi_bus_netlist(parse, "AT45DB321D-SU")
    r = infer_role(ir, "U12", _provider)
    assert r is not None
    assert r.rule == "ic_function"
    assert r.data["function"] == "spi_peripheral"
    assert "SPI" in r.title
    assert "存储" not in r.title
    assert "Flash" not in r.title and "闪存" not in r.title
    assert "固件" not in r.summary


def test_spi_adc_is_not_called_memory(parse) -> None:
    """SPI ADC（传感器/转换器类）在 SPI 总线上不得被定性成存储器。"""
    ir = _spi_bus_netlist(parse, "MCP3202")
    r = infer_role(ir, "U12", _provider)
    assert r is not None
    assert r.data["function"] == "spi_peripheral"
    assert "存储" not in r.title


def test_kb_category_proves_spi_memory(parse) -> None:
    """KB 器件类别明确是存储器/Flash 时才允许定性成 SPI 串行存储器。"""
    ir = _spi_bus_netlist(parse, "AT45DB321D-SU")

    def facts_provider(_comp):
        return {"category": "存储器", "subcategory": "Flash芯片"}

    r = infer_role(ir, "U12", _provider, facts_provider=facts_provider)
    assert r is not None
    assert r.rule == "ic_function"
    assert r.data["function"] == "spi_memory"
    assert "存储" in r.title or "Flash" in r.title


def test_kb_non_memory_category_keeps_peripheral(parse) -> None:
    """KB 类别明确是别的器件（如 ADC）时不得定成存储器。"""
    ir = _spi_bus_netlist(parse, "MCP3202")

    def facts_provider(_comp):
        return {"category": "模数转换器", "subcategory": "ADC"}

    r = infer_role(ir, "U12", _provider, facts_provider=facts_provider)
    assert r is not None
    assert r.data["function"] == "spi_peripheral"


# ---------------------------------------------------------------------------
# 信号串联电阻：另一端挂着无源件（中枢节点）时也算串在信号通路上
# ---------------------------------------------------------------------------


def _reset_hub_netlist(parse, other_end: str):
    """R10 串在 IC 信号脚（C2CK）与上拉+去耦中枢节点（RST）之间的形状。

    ``other_end`` 换掉 RST 那一网的成员，用来造反例。
    """
    return parse(
        {
            "R10": ("0805", "1K"),
            "R8": ("0805", "10K"),
            "C9": ("0805", "104"),
            "U1": ("LQFP32", "C8051F320"),
        },
        {
            "C2CK": ["U1-9", "R10-1"],
            "RST": ["R10-2", "R8-2", "C9-1"] if other_end == "passive" else ["R10-2", "J1-1"],
            "+3.3V": ["R8-1"],
            "GND": ["C9-2"],
        },
    )


def test_series_resistor_into_passive_hub_node(parse) -> None:
    """另一端是上拉+电容构成的无源中枢节点时，电阻就是信号串阻。"""
    ir = _reset_hub_netlist(parse, "passive")
    r = infer_role(ir, "R10", _provider)
    assert r is not None
    assert r.rule == "series_signal"
    assert "串联" in r.title
    assert any("R8" in e and "C9" in e for e in r.evidence)


def test_series_resistor_rejects_active_device_on_other_end(parse) -> None:
    """另一端挂着连接器时不能叫"串联电阻"（可能是 CC / 栅极专用电阻）。"""
    ir = parse(
        {"R10": ("0805", "1K"), "U1": ("LQFP32", "C8051F320"), "J1": ("USBFOOT3", "USB")},
        {"C2CK": ["U1-9", "R10-1"], "OUT": ["R10-2", "J1-1"]},
    )
    r = infer_role(ir, "R10", _provider)
    assert r is not None
    assert r.rule != "series_signal"


def test_series_resistor_rejects_rail_side(parse) -> None:
    """从电源轨出发的电阻是上拉/下拉/滤波支路，不是信号串阻。"""
    ir = parse(
        {"R10": ("0805", "1K"), "C9": ("0805", "104"), "U1": ("LQFP32", "C8051F320")},
        {"+3.3V": ["U1-3", "R10-1"], "N1": ["R10-2", "C9-1"], "GND": ["C9-2"]},
    )
    r = infer_role(ir, "R10", _provider)
    assert r is not None
    assert r.rule != "series_signal"


# ---------------------------------------------------------------------------
# 网表漏导出下臂：悬空电阻疑似分压下臂
# ---------------------------------------------------------------------------


def _dangling_lower_arm_netlist(parse, dangling: list[str], sibling_value: str = "100K 1%"):
    """上臂 R11 接在 +4.2V 与采集点之间，下臂在网表里没有连接记录。"""
    components = {
        "R11": ("0805", sibling_value),
        "U1": ("LQFP32", "C8051F320"),
    }
    for d in dangling:
        components[d] = ("0805", sibling_value)
    nets = {"+4.2V": ["R11-1"], "VBATT": ["R11-2", "U1-16"]}
    return parse(components, nets)


def test_dangling_resistor_is_suspected_divider_lower_arm(parse) -> None:
    """上臂已被识别、悬空件与它同封装同值 → 疑似缺失的分压下臂。"""
    ir = _dangling_lower_arm_netlist(parse, ["R12"])
    r = infer_role(ir, "R12", _provider)
    assert r is not None
    assert r.rule == "sense_divider_lower_arm"
    assert "疑似" in r.title
    assert "下臂" in r.title
    assert "+4.2V" in r.title
    assert r.confidence == "low"


def test_dangling_resistor_without_upper_arm_stays_unconnected(parse) -> None:
    """没有可认的上臂时不得编功能，如实报"未连接"。"""
    ir = parse({"R12": ("0805", "100K 1%")}, {})
    r = infer_role(ir, "R12", _provider)
    assert r is not None
    assert r.rule == "unconnected"


def test_dangling_resistor_with_other_value_stays_unconnected(parse) -> None:
    """值/封装与上臂不同 → 不是同一条分压的下臂。"""
    ir = parse(
        {"R11": ("0805", "100K 1%"), "R12": ("0603", "10K"), "U1": ("LQFP32", "C8051F320")},
        {"+4.2V": ["R11-1"], "VBATT": ["R11-2", "U1-16"]},
    )
    r = infer_role(ir, "R12", _provider)
    assert r is not None
    assert r.rule == "unconnected"


def test_ambiguous_dangling_resistors_stay_unconnected(parse) -> None:
    """同值同封装的悬空件不止一个 → 说不清是哪个下臂，一律不猜。"""
    ir = _dangling_lower_arm_netlist(parse, ["R12", "R13"])
    for designator in ("R12", "R13"):
        r = infer_role(ir, designator, _provider)
        assert r is not None
        assert r.rule == "unconnected"
