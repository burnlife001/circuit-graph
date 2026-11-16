"""End-to-end coverage: every primitive template must fire on at least one
real Protel .NET fixture.

Most cases run the **minimal** enricher chain (MultiUnitSplitter ->
NetClassifier -> EdgeSemanticEnricher -> TopologyRecognizer) — this keeps
existing coverage byte-for-byte unchanged. A case may opt into the **full**
CLI chain (MultiUnitSplitter -> ValueNormalizer -> NetClassifier ->
PinNameResolver -> EdgeSemanticEnricher -> TopologyRecognizer with a
pin_category_provider) by passing chain="full" as a 5th tuple element — only
templates that rely on KB-resolved pin roles to disambiguate (e.g. the
generic ``buck-converter`` template, which has no strict pin numbers and
needs PinNameResolver + role gating to tell a buck IC's VIN/SW/FB apart
from an opamp's V+/OUT/IN-) need this.

Fixtures are recognised once per module per chain and cached — recognition
on the large boards (FX-T268, 500W series) is the slow part.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from circuit_graph.enrichers import run_enrichers
from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher
from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
from circuit_graph.enrichers.net_classifier import NetClassifier
from circuit_graph.enrichers.pin_resolver import PinNameResolver
from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
from circuit_graph.enrichers.value_normalizer import ValueNormalizer
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser
from circuit_graph.primitives import load_primitives

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "protel"

# (primitive_id, fixture_file, expected_block_id_prefix, expected_component_subset[, chain])
# chain omitted/"min" -> minimal chain (original behaviour, untouched).
# chain="full"        -> CLI-equivalent chain (ValueNormalizer + PinNameResolver +
#                         pin_category_provider); only needed when a template
#                         relies on KB-resolved pin roles instead of strict pin
#                         numbers/values to disambiguate against other IC types.
CASES = [
    # --- amplifier ---
    ("differential-comparator", "500W-TX-Main-Zero.NET", "differential-comparator-u5", {"U5"}),
    ("inverting-amplifier", "FX-T268-V6-10米-5065.NET", "inverting-amplifier-u7", {"U7"}),
    ("opamp-unit-differential-amplifier", "add-primitives.NET",
     "opamp-unit-differential-amplifier-u2b", {"U2B", "R4"}),
    ("opamp-unit-voltage-follower", "add-primitives.NET",
     "opamp-unit-voltage-follower-u2c", {"U2C"}),
    ("sallen-key-low-pass", "extra-primitives.NET", "sallen-key-low-pass-u1",
     {"U1", "R1", "R2", "C1", "C2"}),
    # --- filter ---
    ("lc-pi-filter", "500W-RX.NET", "lc-pi-filter-c14", {"C14"}),
    ("rc-high-pass", "500W-RX.NET", "rc-high-pass-c3", {"C3"}),
    # rc-low-pass: expected fixture/block/anchor changed from
    # ("500W-RX.NET", "rc-low-pass-c1", {"C1"}) to
    # ("FX-T268-V6-10米-5065.NET", "rc-low-pass-c7", {"C7"}) — the repo owner retuned
    # rc-low-pass's value_pattern (primitives/filter/rc-low-pass.json, now requiring
    # strict n/pF capacitor values) independently of this task's enricher-chain work.
    # C1 on 500W-RX.NET no longer satisfies the new pattern (no rc-low-pass block
    # fires there at all now); C7 on FX-T268 does. Verified empirically with the
    # minimal chain; kept as-is per instructions (unrelated to B2.1's chain change).
    ("rc-low-pass", "FX-T268-V6-10米-5065.NET", "rc-low-pass-c7", {"C7"}),
    # --- interface ---
    ("fet-level-shifter", "extra-primitives.NET", "fet-level-shifter-q1", {"Q1", "R3", "R4"}),
    ("led-driver-switch", "500W-TX-Main-Zero.NET", "led-driver-switch-esd2",
     {"ESD2", "R11", "RF1"}),
    # opamp-noninv-feedback: 靠 KB 引脚角色（+ / - / OUT）区分同相放大器反馈对，
    # 最小链路没有 PinNameResolver + provider，认不出来 → 走 full 链路。
    ("opamp-noninv-feedback", "FX-T268-V6-10米-5065.NET", "opamp-noninv-feedback-r100",
     {"R100", "R97"}, "full"),
    # --- power ---
    ("bootstrap-cap", "FX-T268-V6-10米-5065.NET", "bootstrap-cap-c27", {"C27"}),
    ("bridge-rectifier-package", "500W-TX-Main-Zero.NET", "bridge-rectifier-package-bd1", {"BD1"}),
    ("bridge-rectifier", "add-primitives.NET", "bridge-rectifier-d1", {"D1"}),
    # current-shunt-monitor 的 value_pattern 收紧为真正的小阻值（T193 终审发现
    # \d+[RΩ] 会把 20R/100R 也当采样电阻）后，433_RX.NET 的 R1（20R，VDD↔VCC）
    # 不再命中——那本来就不是采样电阻。改用专用合成 fixture：R1=10mR 应命中，
    # R2=100R 不应命中（见下方负例测试）。
    ("current-shunt-monitor", "current-shunt.NET", "current-shunt-monitor-r1", {"R1"}),
    ("dc-decoupling-cap", "FX-T268-V6-10米-5065.NET", "dc-decoupling-cap-c1", {"C1", "C2", "C3"}),
    ("detector-amp-sampling", "FX-T268-V6-10米-5065.NET", "detector-amp-sampling-c49",
     {"C49", "C55", "D2"}),
    ("integrated-bridge-rectifier", "extra-primitives.NET",
     "integrated-bridge-rectifier-bd1", {"BD1"}),
    ("peak-envelope-detector", "FX-T268-V6-10米-5065.NET",
     "peak-envelope-detector-c56", {"D2", "R54", "R49", "R58", "C56"}),
    ("resonant-commutation-cap", "FX-T268-V6-10米-5065.NET", "resonant-commutation-cap-c13",
     {"C13", "C14"}),
    ("voltage-divider", "500W-RX.NET", "voltage-divider-r1", {"R1"}),
    # voltage-divider-signal: 信号源→分压→MCU ADC（vin 不接 IC、vout 接 IC）。
    ("voltage-divider-signal", "FX-T268-V6-10米-5065.NET", "voltage-divider-signal-r45",
     {"R45", "R50"}),
    # jw5065-buck: strict_pin_matching + physical pin numbers + strict_value
    # (value_pattern "JW5065") — commits with the minimal chain, no provider needed.
    # C70 是 BST↔bst_mid 的自举电容（C_bst 槽位），必须与芯片同块命中。
    ("jw5065-buck", "FX-T268-V6-10米-5065.NET", "jw5065-buck-u4", {"U4", "C70"}),
    # buck-converter: logical pin names (VIN/SW/FB/GND/BST/EN) disambiguated purely
    # by KB-resolved pin role, so it needs PinNameResolver + pin_category_provider —
    # the full CLI chain.
    ("buck-converter", "FX-T268-V6-10米-5065.NET", "buck-converter-u4", {"U4"}, "full"),
    # sw5001-full-bridge: SW5001 全桥功率级 —— strict_pin_matching + 物理引脚号
    # (VIN/SW1/SW2/PWM1/PWM2/BST1/BST2/PGND/VDD) + strict_value "SW5001"。
    # 走最小链路即可：引脚号与型号都不依赖 KB 引脚名。
    ("sw5001-full-bridge", "FX-T268-V6-10米-5065.NET", "sw5001-full-bridge-q7", {"Q7"}),
    # Q5 与 Q7 同为 SW5001，但 Q5 的 PGND/GND 并在一个网络上（PGND 信号不再
    # 要求与 GND 信号分网），修复后 Q5 也必须命中 —— 防"模板只认 Q7 的接法"。
    ("sw5001-full-bridge", "FX-T268-V6-10米-5065.NET", "sw5001-full-bridge-q5", {"Q5"}),
    # --- protection ---
    ("flyback-diode", "extra-primitives.NET", "flyback-diode-d1", {"D1", "L1"}),
    ("rc-snubber", "FX-T268-V6-10米-5065.NET", "rc-snubber-c9", {"C9", "R26"}),
    ("rc-snubber-series", "FX-T268-V6-10米-5065.NET", "rc-snubber-series-c7", {"C7", "R25"}),
    ("rc-snubber-shunt", "FX-T268-V6-10米-5065.NET", "rc-snubber-shunt-c10", {"C10"}),
    ("tvs-protection", "500W-TX-Main.NET", "tvs-protection-d5", {"D5"}),
    # serial-line-current-limit: 串口线限流电阻 —— 靠"IC 数字脚网（RXD/TXD）↔
    # 无 IC 脚网（连接器侧）"的结构 + 100~699Ω 阻值定位，不吃上拉/下拉。
    ("serial-line-current-limit", "T193.NET", "serial-line-current-limit-r13", {"R13"}),
    # --- switch ---
    ("nmos-low-side-switch", "500W-TX-Main-Zero.NET", "nmos-low-side-switch-q1", {"Q1"}),
    ("npn-low-side-switch", "500W-TX-Main-Zero.NET", "npn-low-side-switch-esd2", {"ESD2"}),
    ("pmos-high-side-switch", "FX-T268-V6-10米-5065.NET", "pmos-high-side-switch-q2", {"Q2"}),
    # bjt-npn-low-side-switch: 真 NPN 双极管（8050）。IR 无 BJT 类型，Protel
    # 解析器把 Q* 一律判成 Mosfet，故模板用 type=Mosfet + strict_value 型号
    # 白名单锁住 NPN 型号，再用 C/E/B 引脚角色（KB 引脚名）对上集电极/基极。
    ("bjt-npn-low-side-switch", "T193.NET", "bjt-npn-low-side-switch-q1", {"Q1"}),
    # --- termination ---
    ("parallel-termination", "extra-primitives.NET", "parallel-termination-r5", {"R5", "R6"}),
    ("pull-down-resistor", "500W-RX.NET", "pull-down-resistor-r2", {"R2"}),
    ("pull-up-resistor", "433_RX.NET", "pull-up-resistor-r3", {"R3"}),
    ("type-c-cc-pull-down", "FX-T268-V6-10米-5065.NET", "type-c-cc-pull-down-r1", {"R1"}),
    # --- timing ---
    ("crystal-with-load-caps", "add-primitives.NET", "crystal-with-load-caps-c1", {"C1"}),
    # rc-reset-network: MCU 上电复位 RC（上拉电阻 + 复位脚对地电容）。
    ("rc-reset-network", "T193.NET", "rc-reset-network-c9", {"R8", "C9"}),
]


def _build_pin_category_provider():
    """Full-chain provider — 复用 CLI 的实现，避免两处逻辑漂移。

    与 ``circuit_graph/cli/app.py::build_pin_category_provider`` 是同一份逻辑；
    那边若改动，这里要跟着改（或把两边合并到同一个模块）。
    """
    from circuit_graph.cli.app import build_pin_category_provider

    return build_pin_category_provider()


def _recognize(fixture_name: str, chain: str = "min"):
    netlist = FIXTURE_DIR / fixture_name
    if not netlist.exists():
        pytest.skip(f"Fixture not found: {netlist}")
    ir = ProtelNetParser().parse(ParserInput(file_path=str(netlist)))

    if chain == "full":
        enrichers = [
            MultiUnitSplitter(),
            ValueNormalizer(),
            NetClassifier(),
            PinNameResolver(),
            EdgeSemanticEnricher(),
            TopologyRecognizer(pin_category_provider=_build_pin_category_provider()),
        ]
    else:
        enrichers = [MultiUnitSplitter(), NetClassifier(), EdgeSemanticEnricher(), TopologyRecognizer()]

    return run_enrichers(ir, enrichers).ir


@pytest.fixture(scope="module")
def recognized_cache():
    return {}


def _case_id(c):
    chain = c[4] if len(c) > 4 else "min"
    suffix = f"-{chain}" if chain != "min" else ""
    return f"{c[0]}-on-{c[1]}{suffix}"


@pytest.mark.parametrize(
    "case",
    CASES,
    ids=[_case_id(c) for c in CASES],
)
def test_primitive_fires_on_fixture(case, recognized_cache):
    primitive_id, fixture, block_prefix, expected_comps = case[0], case[1], case[2], case[3]
    chain = case[4] if len(case) > 4 else "min"

    cache_key = (fixture, chain)
    if cache_key not in recognized_cache:
        recognized_cache[cache_key] = _recognize(fixture, chain)
    ir2 = recognized_cache[cache_key]

    blocks = [b for b in ir2.blocks if b.id.startswith(block_prefix)]
    assert blocks, (
        f"No block starting with {block_prefix!r} in {fixture} (chain={chain}); "
        f"got: {sorted(b.id for b in ir2.blocks)}"
    )
    assert any(expected_comps <= set(b.component_ids) for b in blocks), (
        f"Expected {block_prefix} block covering {sorted(expected_comps)}; "
        f"got: {[(b.id, sorted(b.component_ids)) for b in blocks]}"
    )


def test_every_primitive_has_a_fixture_case():
    """Guard: adding a new primitive JSON without a fixture case fails here."""
    template_ids = {t.id for t in load_primitives()}
    covered = {c[0] for c in CASES}
    missing = template_ids - covered
    assert not missing, f"Primitives without a fixture case: {sorted(missing)}"


def test_current_shunt_does_not_fire_on_ordinary_resistance():
    """负例：100R 的普通电阻不得命中采样电阻模板（小阻值语义守护）。

    T193 终审发现旧 value_pattern ``\d+(\.\d+)?[RΩ]`` 没有上限，把
    433_RX.NET 的 20R、T193 的 100K 分压电阻都误判成采样电阻。收紧后，
    current-shunt.NET 里 100R 的 R2 不得再产生 current-shunt 块。
    """
    ir = _recognize("current-shunt.NET")
    shunt_blocks = [b for b in ir.blocks if b.id.startswith("current-shunt-monitor")]
    assert [b.component_ids for b in shunt_blocks] == [["R1"]], (
        f"current-shunt.NET 上只允许 R1（10mR）命中采样电阻；got: "
        f"{[(b.id, b.component_ids) for b in shunt_blocks]}"
    )
