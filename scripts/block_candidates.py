"""生成 easyeda-agent 38 个功能块 → circuit-graph primitives 模板的候选清单。

只读 blocks 快照与 KB 种子快照，产出派生报告 ``output/block-candidates.md``：
不写库、不改 primitives/、不碰 fixtures。

用法::

    python scripts/block_candidates.py [--blocks-dir DIR] [--out FILE]
    python scripts/block_candidates.py --probe block.sp3485_rs485_halfduplex

``REVIEW`` 是人工判断表（目标 primitives 目录 / 现有模板覆盖状态 / 缺口说明），
新增 block 未登记时会直接报错，避免清单静默漏项。

``--probe`` 用真实识别链路（与 ``cg primitives coverage`` 同一套 enricher 链）
在**块自身的拓扑**上跑一遍模板匹配：块不是 .NET fixture，无法直接喂给 CLI，
因此把块 JSON 的 parts + internal_nets 还原成 CircuitIR 再匹配。结果写进
``output/block-probe.json``，清单据此给覆盖结论标「实测 / 未实测」。
"""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BLOCKS_DIR = Path("E:/__electric/__workspace/stacking/easyeda-agent/internal/blocks/data")
DEFAULT_SNAPSHOT_DIR = ROOT / "circuit_graph" / "knowledge" / "seed" / "easyeda"
DEFAULT_OUTPUT = ROOT / "output" / "block-candidates.md"
DEFAULT_PROBE_OUT = ROOT / "output" / "block-probe.json"

PASSIVE_PREFIXES = frozenset({"res", "cap", "ind"})

# block id → (目标 primitives 目录, 覆盖状态, 现有模板, 缺口说明)
REVIEW: dict[str, tuple[str, str, str, str]] = {
    "block.ams1117_ldo_3v3": ("power/", "未覆盖", "—", "没有 LDO/线性稳压模板"),
    "block.ant_2g4_ceramic_pi": (
        "filter/", "部分覆盖", "lc-pi-filter",
        "C-L-C 拓扑可被 π 型滤波命中，但天线匹配/净空语义没有模板",
    ),
    "block.aw8737_classd_spk": (
        "amplifier/", "未覆盖", "—", "无 D/K 类功放模板（现有 amplifier/ 只覆盖运放）",
    ),
    "block.axp2101_pmu": (
        "power/", "未覆盖", "—", "PMU 多路输出电源树无模板",
    ),
    "block.bmi270_imu_i2c": (
        "sensor/", "未覆盖", "—", "sensor/ 现有模板都是检波/采样，无 I2C 传感器最小系统",
    ),
    "block.bq24074_powerpath_charger": (
        "power/", "未覆盖", "—", "充电器 + power-path 无模板",
    ),
    "block.cc1101_433m_balun_ipex": (
        "filter/", "未覆盖", "—", "分立 LC 巴伦/两级匹配无模板，lc-pi-filter 不匹配该拓扑",
    ),
    "block.ch334f_usb4_hub": (
        "interface/", "未覆盖", "—", "USB hub 无模板",
    ),
    "block.ch340c_usb_serial": (
        "interface/", "部分覆盖", "type-c-cc-pull-down",
        "CC 5.1k 下拉可命中，USB 转串口主体与 ESD 阵列无模板",
    ),
    "block.es8311_codec_i2s": (
        "interface/", "未覆盖", "—", "audio codec（I2C+I2S）无模板",
    ),
    "block.esp32_autodownload": (
        "transistor/", "未覆盖", "npn-low-side-switch",
        "单管可被 NPN 低边开关命中，但 DTR/RTS 交叉耦合双管时序语义没有模板",
    ),
    "block.esp32s3_chip_minsys": (
        "timing/", "部分覆盖", "crystal-with-load-caps, rc-reset-network, pull-up-resistor",
        "晶振/复位 RC/上拉子网可命中，整块最小系统（外挂 flash+PSRAM）无模板",
    ),
    "block.esp32s3_pico_native_usb": (
        "interface/", "部分覆盖", "rc-reset-network, pull-up-resistor",
        "复位 RC 与 I2C 上拉子网可命中，SiP 最小系统 + 原生 USB 无模板",
    ),
    "block.esp32s3_wroom1_module": (
        "timing/", "部分覆盖", "dc-decoupling-cap, pull-up-resistor",
        "EN/IO0 上拉（R_EN 10k、R_IO0 10k）与 VDD 去耦可命中；"
        "rc-reset-network 不命中（本块用 EN 命名 + C_EN 1µF，模板要求 RST 命名 + 100nF）；"
        "模组最小系统整体无模板",
    ),
    "block.esp32s3r8_chip_minsys": (
        "timing/", "部分覆盖", "crystal-with-load-caps, rc-reset-network, pull-up-resistor",
        "晶振/复位 RC/上拉子网可命中，裸片最小系统整体无模板",
    ),
    "block.i2c_isolation_2n7002dw": (
        "interface/", "未覆盖", "—",
        "fet-level-shifter 是单管双向电平转换；背靠背双管域隔离拓扑不同，不能套用",
    ),
    "block.ina226_power_monitor": (
        "passive/", "部分覆盖", "current-shunt-monitor",
        "10mΩ 采样电阻可命中，INA226 高侧监测 IC 外围无模板",
    ),
    "block.ir_txrx_remote": (
        "transistor/", "部分覆盖", "nmos-low-side-switch",
        "红外发射管低边驱动可命中，38kHz 接收头前端无模板",
    ),
    "block.lc29h_dr_gnss_frontend": (
        "filter/", "未覆盖", "—", "有源天线 bias-tee + RF ESD/π 占位无模板",
    ),
    "block.led_indicator_gpio": (
        "transistor/", "未覆盖", "—",
        "GPIO 经限流电阻直驱 LED 无模板（led-driver-switch 是三极管驱动）",
    ),
    "block.lgs4056_liion_charge_path": (
        "power/", "未覆盖", "—", "线性充电 + 理想二极管 OR + P-MOS 采样门控无模板",
    ),
    "block.mems_mic_analog": (
        "sensor/", "未覆盖", "—", "模拟 MEMS 麦克风伪差分前端无模板",
    ),
    "block.microsd_spi_pushpush": (
        "interface/", "部分覆盖", "pull-up-resistor",
        "各路 10k 上拉可命中，SD 卡座 SPI 接口整体无模板",
    ),
    "block.opto_acc_ign_detect": (
        "protection/", "部分覆盖", "tvs-protection",
        "SMBJ33A 钳位可命中，PTC/限流/光耦隔离前端无模板",
    ),
    "block.pmos_highside_softstart": (
        "transistor/", "部分覆盖",
        "bjt-npn-low-side-switch, pmos-high-side-switch, pull-up-resistor",
        "P-MOS 高边主拓扑（Q_P）、NPN 低边驱动（Q_N）、栅极上拉可命中；"
        "软启动 RC（C_GS/R_SLEW）与基极电阻无模板",
    ),
    "block.sdnand_sdmmc_4bit": (
        "interface/", "部分覆盖", "pull-up-resistor",
        "CMD/DAT 上拉与 CLK 串阻可命中，SD-NAND 焊死存储接口无模板",
    ),
    "block.sim_card_6p_socket": (
        "protection/", "部分覆盖", "tvs-protection",
        "四路 ESD9B5.0 可命中，SIM 卡座接口（串阻/去耦/上拉）无模板",
    ),
    "block.sp3485_rs485_halfduplex": (
        "protection/", "部分覆盖",
        "dc-decoupling-cap, pull-down-resistor, pull-up-resistor, voltage-divider-signal",
        "偏置/终端电阻（680R 上下拉、120R）与 VCC 去耦可命中；"
        "tvs-protection 不命中（SM712 两数据脚同名 A1、K 接地，保护端是信号不是电源）；"
        "RS-485 收发器 U7、TVS D701、连接器无模板",
    ),
    "block.st7789_spi_lcd_btb": (
        "transistor/", "部分覆盖", "nmos-low-side-switch",
        "背光低边开关可命中，BTB 显示接口无模板",
    ),
    "block.sy7088_boost_5v": (
        "power/", "未覆盖", "—", "同步升压无模板（power/ 只有 buck 类）",
    ),
    "block.sy8089_buck_3v3": (
        "power/", "已覆盖", "buck-converter", "同步 buck 主拓扑已能被现有模板识别",
    ),
    "block.tactile_boot_reset": (
        "interface/", "未覆盖", "—", "轻触按键/BOOT 下载无模板",
    ),
    "block.tps63802_buckboost_3v8": (
        "power/", "未覆盖", "—", "buck-boost 无模板（buck-converter 拓扑不同，不能套用）",
    ),
    "block.uart_power_header_1x4": (
        "不适用（PCB/丝印类）", "不适用", "—", "纯连接器网络，无器件级电路语义",
    ),
    "block.usbc_dual_orientation_data": (
        "interface/", "部分覆盖", "type-c-cc-pull-down, tvs-protection",
        "CC 5.1k 下拉与数据 ESD 可命中，DP/DM 双取向短接只是连线",
    ),
    "block.usbc_ufp_power_or": (
        "protection/", "部分覆盖", "tvs-protection",
        "SMAJ5.0A/USBLC6 可命中，VBUS 二极管 OR 无模板",
    ),
    "block.vehicle_input_tps54360_5v": (
        "power/", "部分覆盖", "buck-converter, flyback-diode",
        "buck 主拓扑与续流管可命中，反接 P-MOS/栅极钳位/UVLO/COMP 无模板",
    ),
    "block.xl1509_buck_12v_5v": (
        "power/", "部分覆盖", "flyback-diode",
        "续流 SS34 可命中；固定 5V 版 FEEDBACK 直连、无外部反馈分压，buck 模板不匹配",
    ),
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_blocks(blocks_dir: Path) -> list[dict[str, Any]]:
    blocks = []
    for path in sorted(blocks_dir.glob("*.json")):
        if path.name.startswith("_"):
            continue
        block = _load_json(path)
        block["_file"] = path.name
        blocks.append(block)
    return blocks


def _pin_text(pins: list[dict[str, Any]]) -> str:
    items = []
    for pin in pins or []:
        number = str(pin.get("n") or "").strip()
        name = str(pin.get("name") or "").strip()
        if number and name:
            items.append(f"{number}={name}")
    return "、".join(items)


def _is_passive(slug: str) -> bool:
    return slug.split(".", 1)[0].strip().lower() in PASSIVE_PREFIXES


def _parts_text(block: dict[str, Any], parts: dict[str, Any], pins: dict[str, Any]) -> str:
    chunks = []
    for role, spec in (block.get("parts") or {}).items():
        slug = str(spec.get("part") or "")
        kind = str(spec.get("kind") or "").strip()
        label = f"{role}={slug}" + (f"({kind})" if kind else "")
        pin_text = _pin_text(pins.get(slug) or [])
        if pin_text and not _is_passive(slug):
            label += f" [{pin_text}]"
        chunks.append(label)
    return "；".join(chunks)


def _net_text(block: dict[str, Any]) -> str:
    nets = block.get("internal_nets") or block.get("nets") or []
    return "；".join(" — ".join(str(x) for x in net) for net in nets)


def _ports_text(block: dict[str, Any]) -> str:
    return ", ".join(
        f"{name}:{spec.get('dir', '?')}" for name, spec in (block.get("ports") or {}).items()
    )


def _collect_missing_pinouts(
    blocks: list[dict[str, Any]], parts: dict[str, Any], pins: dict[str, Any]
) -> list[dict[str, Any]]:
    """非无源、有 MPN、但快照里没有引脚表的条目（快照口径，重复 slug 各算一条）。"""
    owners: dict[str, list[str]] = {}
    for block in blocks:
        for spec in (block.get("parts") or {}).values():
            slug = str(spec.get("part") or "")
            owners.setdefault(slug, []).append(block.get("id"))

    rows: list[dict[str, Any]] = []
    for slug, entry in parts.items():
        if not isinstance(entry, dict) or _is_passive(slug):
            continue
        mpn = str(entry.get("mpn") or "").strip()
        if _pin_text(pins.get(slug) or []):
            continue
        same_mpn = [
            other
            for other, other_entry in parts.items()
            if mpn
            and isinstance(other_entry, dict)
            and str(other_entry.get("mpn") or "").strip().upper() == mpn.upper()
            and _pin_text(pins.get(other) or [])
        ]
        note = "—"
        if same_mpn:
            note = f"同 MPN 另有有引脚表 slug：{'、'.join(f'`{s}`' for s in sorted(same_mpn))}"
        rows.append(
            {
                "slug": slug,
                "mpn": mpn,
                "blocks": sorted(set(owners.get(slug) or [])),
                "note": note,
            }
        )
    return sorted(rows, key=lambda row: (row["mpn"].upper(), row["slug"]))


def _collect_candidates(
    blocks: list[dict[str, Any]], parts: dict[str, Any], pins: dict[str, Any]
) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for block in blocks:
        for spec in (block.get("parts") or {}).values():
            slug = str(spec.get("part") or "")
            if _is_passive(slug):
                continue
            mpn = str((parts.get(slug) or {}).get("mpn") or "").strip()
            pin_text = _pin_text(pins.get(slug) or [])
            if not mpn or not pin_text:
                continue
            entry = found.setdefault(mpn, {"mpn": mpn, "pins": pin_text, "blocks": []})
            entry["blocks"].append(block.get("id"))
    for entry in found.values():
        entry["blocks"] = sorted(set(entry["blocks"]))
    return sorted(found.values(), key=lambda item: item["mpn"])


# ---------------------------------------------------------------------------
# 实测探针：把块 JSON 还原成 CircuitIR，跑真实模板匹配
# ---------------------------------------------------------------------------

# slug 前缀 → IR 器件类型（位号前缀无法判定时才用，如 SW/FB/ANT）。
_KIND_BY_SLUG = {
    "res": "Resistor",
    "cap": "Capacitor",
    "ind": "Inductor",
    "diode": "Diode",
    "tvs": "Diode",
    "esd": "Diode",
    "led": "Diode",
    "pmos": "Mosfet",
    "nmos": "Mosfet",
    "bjt": "Mosfet",
    "mosfet": "Mosfet",
    "conn": "Connector",
    "connector": "Connector",
}

# 位号前缀 → IR 器件类型，与 parsers/protel.py::_infer_device_kind 同序。
_KIND_BY_REF = {
    "R": "Resistor",
    "C": "Capacitor",
    "L": "Inductor",
    "U": "IC",
    "Q": "Mosfet",
    "D": "Diode",
    "J": "Connector",
    "P": "Connector",
}

_VALUE_SHORT_RE = re.compile(r"^(\d+)([pnu])(\d+)$")


def _device_kind(ref: str, slug: str) -> str:
    """位号前缀优先（与 Protel 解析一致），判不出再看 slug 前缀。"""
    if ref.upper().startswith("LED"):
        return "Diode"
    m = re.match(r"[A-Za-z]+", ref or "")
    prefix = m.group(0).upper() if m else ""
    if prefix in _KIND_BY_REF:
        return _KIND_BY_REF[prefix]
    if prefix.startswith("IC"):
        return "IC"
    return _KIND_BY_SLUG.get(slug.split(".", 1)[0].strip().lower(), "Unknown")


def _value_text(slug: str) -> str | None:
    """``res.10k_0402`` → ``10K``；``cap.2u2_0805`` → ``2.2UF``。"""
    body = slug.split(".", 1)[1] if "." in slug else slug
    token = body.split("_", 1)[0].strip()
    if not token:
        return None
    short = _VALUE_SHORT_RE.match(token)
    if short:
        whole, unit, frac = short.groups()
        token = f"{whole}.{frac}{unit.upper()}"
    prefix = slug.split(".", 1)[0].lower()
    upper = token.upper()
    if prefix == "cap" and re.fullmatch(r"[\d.]+[PNU]", upper):
        upper += "F"
    elif prefix == "ind" and re.fullmatch(r"[\d.]+[PNU]", upper):
        upper += "H"
    return upper


def _pin_index(pin_list: list[dict[str, Any]]) -> dict[str, list[str]]:
    """``[{"n":"2","name":"S"}]`` → ``{"S": ["2"]}``（同名多脚如 GND 会合并）。"""
    index: dict[str, list[str]] = {}
    for pin in pin_list:
        number = str(pin.get("n") or "").strip()
        name = str(pin.get("name") or "").strip().upper()
        if number and name:
            index.setdefault(name, []).append(number)
    return index


def _block_ir(
    block: dict[str, Any], parts: dict[str, Any], pins: dict[str, Any]
):
    """把块 JSON 还原成与 .NET 解析同等形状的 CircuitIR。

    * 器件：位号 → Component，型号取快照 MPN，引脚号/名取 symbol-pins；
      无引脚表的器件只能按块内引用的引脚名占位（号码未知），模板的
      物理引脚号约束会因此不命中 —— 这与 T2「无引脚表」的实际情况一致。
    * 网络：含 ``PORT:xxx`` 的用端口 ``default_net`` 命名；纯内部网络按
      Protel 风格自动命名 ``Net<n>``（不编造语义名，避免白送命名分）。
    """
    from circuit_graph.ir import (
        CircuitIR,
        CircuitMeta,
        Component,
        Connection,
        DeviceKind,
        Net,
        Pin,
        make_pin_id,
    )

    ports = block.get("ports") or {}
    pin_maps: dict[str, dict[str, list[str]]] = {}
    components: list[Component] = []

    for ref, spec in (block.get("parts") or {}).items():
        slug = str(spec.get("part") or "")
        meta = parts.get(slug) or {}
        kind = DeviceKind(_device_kind(ref, slug))
        passive = kind in {DeviceKind.RESISTOR, DeviceKind.CAPACITOR, DeviceKind.INDUCTOR}
        pin_list = pins.get(slug) or []
        pin_maps[ref] = _pin_index(pin_list)
        ir_pins = [
            Pin(
                id=make_pin_id(ref, str(pin.get("n")).strip()),
                number=str(pin.get("n")).strip(),
                name=str(pin.get("name")).strip() or None,
            )
            for pin in pin_list
        ]
        components.append(
            Component(
                id=ref,
                device_kind=kind,
                raw_value=_value_text(slug),
                part_number=None if passive else (str(meta.get("mpn") or "").strip() or None),
                pins=ir_pins,
            )
        )

    component_by_ref = {comp.id: comp for comp in components}
    nets: dict[str, Net] = {}
    connections: list[Connection] = []

    def _pin_numbers(ref: str, raw_name: str) -> list[str]:
        name = raw_name.strip().upper()
        if name.endswith("*"):
            name = name[:-1]
        numbers = pin_maps.get(ref, {}).get(name)
        if numbers:
            return list(numbers)
        # 无引脚表（或表里没这个名字）：用引脚名占位，号码未知。
        comp = component_by_ref.get(ref)
        if comp is not None and not any(p.number == raw_name for p in comp.pins):
            comp.pins.append(
                Pin(id=make_pin_id(ref, raw_name), number=raw_name, name=raw_name)
            )
            pin_maps.setdefault(ref, {}).setdefault(name, []).append(raw_name)
        return [raw_name]

    for index, members in enumerate(block.get("internal_nets") or [], 1):
        tokens = [str(t).strip() for t in members]
        net_name: str | None = None
        for token in tokens:
            if not token.upper().startswith("PORT:"):
                continue
            port_key = token.split(":", 1)[1]
            spec = ports.get(port_key) or {}
            net_name = str(spec.get("default_net") or port_key)
            break
        if net_name is None:
            net_name = f"Net{index}"
        nets.setdefault(net_name, Net(id=net_name, name=net_name))
        for token in tokens:
            if token.upper().startswith("PORT:"):
                continue
            ref, _, raw_pin = token.partition(".")
            if not ref or not raw_pin or ref not in component_by_ref:
                continue
            for number in _pin_numbers(ref, raw_pin):
                connections.append(
                    Connection(pin_id=make_pin_id(ref, number), net_id=net_name)
                )

    return CircuitIR(
        meta=CircuitMeta(
            source=str(block.get("id") or "block"), format="easyeda-block"
        ),
        components=components,
        nets=list(nets.values()),
        connections=connections,
    )


def probe_block(
    block: dict[str, Any],
    parts: dict[str, Any],
    pins: dict[str, Any],
    *,
    topology_timeout: float | None = None,
) -> dict[str, Any]:
    """在块拓扑上跑完整识别链，返回实测命中的模板与未命中元件。"""
    from circuit_graph.cli.app import build_pin_category_provider
    from circuit_graph.cli.primitives import _template_of_block
    from circuit_graph.enrichers import run_enrichers
    from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher
    from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
    from circuit_graph.enrichers.net_classifier import NetClassifier
    from circuit_graph.enrichers.pin_resolver import PinNameResolver
    from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
    from circuit_graph.enrichers.value_normalizer import ValueNormalizer
    from circuit_graph.primitives import load_primitives

    ir = _block_ir(block, parts, pins)
    result = run_enrichers(
        ir,
        [
            MultiUnitSplitter(),
            ValueNormalizer(),
            NetClassifier(),
            PinNameResolver(),
            EdgeSemanticEnricher(),
            TopologyRecognizer(
                timeout_seconds=topology_timeout,
                pin_category_provider=build_pin_category_provider(),
            ),
        ],
    )
    ir = result.ir

    template_ids = [t.id for t in load_primitives()]
    hits: dict[str, dict[str, Any]] = {}
    matched: set[str] = set()
    for recognised in ir.blocks:
        template_id = _template_of_block(recognised.id, template_ids)
        if template_id is None:
            continue
        entry = hits.setdefault(template_id, {"id": template_id, "instances": []})
        entry["instances"].append(
            {
                "block": recognised.id,
                "components": sorted(recognised.component_ids),
            }
        )
        matched.update(recognised.component_ids)

    all_refs = sorted(c.id for c in ir.components)
    non_passive = sorted(
        c.id
        for c in ir.components
        if c.device_kind.value
        not in {"Resistor", "Capacitor", "Inductor"}
    )
    return {
        "block": block.get("id"),
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "templates": [hits[key] for key in sorted(hits)],
        "matched_components": sorted(matched),
        "unmatched_non_passive": [ref for ref in non_passive if ref not in matched],
        "components": all_refs,
        "warnings": result.warnings,
    }


def _load_probe(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _probe_summary(probe: dict[str, Any]) -> str:
    """单行摘要：`实测：tvs-protection(d701)`；无命中则 `实测：无命中`。"""
    templates = probe.get("templates") or []
    if not templates:
        return "实测：无命中"
    parts = []
    for entry in templates:
        refs = sorted({ref for inst in entry["instances"] for ref in inst["components"]})
        parts.append(f"{entry['id']}({', '.join(refs)})")
    return "实测：" + "；".join(parts)


def _uncovered_text(probe: dict[str, Any]) -> str:
    refs = probe.get("unmatched_non_passive") or []
    return "、".join(refs) if refs else "—"


def render(
    blocks: list[dict[str, Any]],
    parts: dict[str, Any],
    pins: dict[str, Any],
    probe: dict[str, Any] | None = None,
) -> str:
    missing = [b["id"] for b in blocks if b["id"] not in REVIEW]
    if missing:
        raise SystemExit(f"REVIEW 缺少 block 登记：{', '.join(missing)}")

    lines = [
        "# easyeda-agent 功能块 → primitives 模板候选清单",
        "",
        "> 由 `scripts/block_candidates.py` 自动生成（只读 blocks 快照与 KB 种子快照）。",
        "> 覆盖状态是人工判断：`已覆盖` = 现有模板能完整识别该块主拓扑；",
        "> `部分覆盖` = 仅块内子网络能命中；`未覆盖` = 无对应模板；`不适用` = 无电路语义。",
        "> 「实测」列来自 `--probe` 的真实模板匹配（同一套 enricher 链跑块自身拓扑），",
        "> 未跑过的块标 `未实测`；实测与人工判断不一致时以实测为准并更正本表。",
        f"> 共 {len(blocks)} 个功能块。",
        "",
        "## 总览",
        "",
        "| # | block | 分类 | 目标 primitives 目录 | 覆盖状态 | 实测 | 现有模板 | 缺口 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    probe = probe or {}
    for index, block in enumerate(blocks, 1):
        target, status, templates, gap = REVIEW[block["id"]]
        measured = _probe_summary(probe[block["id"]]) if block["id"] in probe else "未实测"
        lines.append(
            f"| {index} | `{block['id']}` | {block.get('category', '')} | {target} "
            f"| {status} | {measured} | {templates} | {gap} |"
        )

    lines += ["", "## 逐块详情", ""]
    for index, block in enumerate(blocks, 1):
        target, status, templates, gap = REVIEW[block["id"]]
        lines.append(f"### {index}. {block['id']}")
        lines.append("")
        lines.append(f"- 功能：{block.get('desc', '')}")
        lines.append(f"- 目标目录 / 覆盖：{target} · {status}（现有模板：{templates}）")
        lines.append(f"- 缺口：{gap}")
        if block["id"] in (probe or {}):
            entry = probe[block["id"]]
            lines.append(
                f"- 实测：{_probe_summary(entry)}（{entry.get('checked_at', '')}）；"
                f"未命中的非无源元件：{_uncovered_text(entry)}"
            )
        else:
            lines.append("- 实测：未实测")
        lines.append(f"- 器件：{_parts_text(block, parts, pins)}")
        lines.append(f"- 网表：{_net_text(block)}")
        lines.append(f"- 端口：{_ports_text(block)}")
        source = str(block.get("source") or "").replace("\n", " ")
        lines.append(f"- 出处：{source[:300]}")
        lines.append("")

    candidates = _collect_candidates(blocks, parts, pins)
    lines += [
        "## 型号-引脚名候选（去重）",
        "",
        "> 来自快照 `symbol-pins.json`，供人工比对 PRD §四·A1 的缺口型号；**本清单不写 KB**。",
        "",
        "| MPN | 引脚名 | 出现块 |",
        "|---|---|---|",
    ]
    for entry in candidates:
        lines.append(
            f"| `{entry['mpn']}` | {entry['pins']} | " + "、".join(entry["blocks"]) + " |"
        )
    lines.append("")

    missing_pinouts = _collect_missing_pinouts(blocks, parts, pins)
    lines += [
        "## 无引脚表条目（非无源）",
        "",
        "> 快照口径：这些条目有 MPN 但没有 symbol-pins 引脚表，导入后只能靠"
        "板级映射（`pinouts.source_ref`）兜底；**不凭空编造引脚号**。",
        "> 「所属块」为空表示该条目前未被任何 38 块引用（快照目录条目）。",
        "",
        "| # | slug | MPN | 所属块 | 备注 |",
        "|---|---|---|---|---|",
    ]
    for index, row in enumerate(missing_pinouts, 1):
        owners = "、".join(row["blocks"]) if row["blocks"] else "—"
        mpn = f"`{row['mpn']}`" if row["mpn"] else "—（快照无 MPN）"
        lines.append(
            f"| {index} | `{row['slug']}` | {mpn} | {owners} | {row['note']} |"
        )
    lines += ["", f"> 共 {len(missing_pinouts)} 条。", ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="生成功能块 → primitives 模板候选清单")
    ap.add_argument("--blocks-dir", default=str(DEFAULT_BLOCKS_DIR))
    ap.add_argument("--snapshot-dir", default=str(DEFAULT_SNAPSHOT_DIR))
    ap.add_argument("--out", default=str(DEFAULT_OUTPUT))
    ap.add_argument(
        "--probe",
        action="append",
        default=None,
        metavar="BLOCK_ID",
        help="实测该块（可重复）：跑真实模板匹配并写入 block-probe.json",
    )
    ap.add_argument("--probe-out", default=str(DEFAULT_PROBE_OUT))
    args = ap.parse_args(argv)

    blocks = _load_blocks(Path(args.blocks_dir))
    snapshot = Path(args.snapshot_dir)
    parts = _load_json(snapshot / "standard-parts.json").get("parts") or {}
    pins = _load_json(snapshot / "symbol-pins.json").get("parts") or {}

    probe_path = Path(args.probe_out)
    probe = _load_probe(probe_path)
    if args.probe:
        by_id = {block["id"]: block for block in blocks}
        for block_id in args.probe:
            if block_id not in by_id:
                raise SystemExit(f"未知 block：{block_id}")
            result = probe_block(by_id[block_id], parts, pins)
            probe[block_id] = result
            for entry in result["templates"]:
                print(f"{block_id} -> {entry['id']}: {_probe_summary({'templates': [entry]})}")
            if not result["templates"]:
                print(f"{block_id} -> 无模板命中")
            print(
                f"{block_id} 未命中非无源元件：{_uncovered_text(result)}"
            )
        probe_path.parent.mkdir(parents=True, exist_ok=True)
        probe_path.write_text(
            json.dumps(probe, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"probe written: {probe_path}")

    text = render(blocks, parts, pins, probe)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")

    status_count: dict[str, int] = {}
    for block in blocks:
        status = REVIEW[block["id"]][1]
        status_count[status] = status_count.get(status, 0) + 1
    print(f"blocks={len(blocks)} candidates={len(_collect_candidates(blocks, parts, pins))}")
    print("coverage:", ", ".join(f"{k}={v}" for k, v in sorted(status_count.items())))
    print(f"written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
