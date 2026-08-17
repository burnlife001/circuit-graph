# Primitives 索引

> 由 [`scripts/build_primitive_index.py`](./scripts/build_primitive_index.py) 自动生成于 2026-08-17，共 21 个基元。请勿手改。

| ID | 名称 | 用途 | 严格模式 | 文件 |
|---|---|---|---|---|
| `bridge-rectifier` | 桥式整流器 | 4 个二极管组成全波整流桥：2 个 AC 输入，1 个 DC+ 输出，1 个 DC- 输出（通常接地）。 | — | [bridge-rectifier.json](./bridge-rectifier.json) |
| `crystal-with-load-caps` | 晶振 + 负载电容 | 晶体 / 陶瓷谐振器两端各接一个负载电容到地（Pierce 振荡器拓扑）。 | — | [crystal-with-load-caps.json](./crystal-with-load-caps.json) |
| `current-shunt-monitor` | 电流采样电阻 | 小阻值精密电阻串联在电源路径中，两端压降反映电流；常配合电流检测放大器使用。 | role | [current-shunt-monitor.json](./current-shunt-monitor.json) |
| `differential-amplifier` | 差分放大器 | 运放差分放大：R1/Rin1将VIN1接入反相端，R2/Rin2将VIN2接入同相端，Rf跨接输出与反相端，Rg连接同… | — | [differential-amplifier.json](./differential-amplifier.json) |
| `fet-level-shifter` | FET 双向电平转换 | NMOS + 两个上拉电阻实现双向电平转换（常见于 I2C 5V↔3V3）。 | level, role | [fet-level-shifter.json](./fet-level-shifter.json) |
| `flyback-diode` | 续流二极管 | 二极管反向并联在电感/继电器线圈两端，关断时为感应电流提供续流路径。 | role | [flyback-diode.json](./flyback-diode.json) |
| `inverting-amplifier` | 反向比例放大器 | 运放反向比例放大：输入信号经Rin接入反相端(-)，反馈电阻Rf跨接输出与反相端，同相端(+)接地，增益=-Rf/Ri… | — | [inverting-amplifier.json](./inverting-amplifier.json) |
| `lc-pi-filter` | π 型 LC 滤波器 | C-L-C π 型滤波：输入电容 + 串联电感 + 输出电容，用于电源纹波抑制。 | role | [lc-pi-filter.json](./lc-pi-filter.json) |
| `nmos-low-side-switch` | NMOS 低边开关 | NMOS 漏极接负载，源极接地，栅极接控制信号。 | role | [nmos-low-side-switch.json](./nmos-low-side-switch.json) |
| `non-inverting-amplifier` | 同相放大器 | 运放同相放大：输入信号接同相端(+)，反馈电阻Rf跨接输出与反相端(-)，Rg连接反相端到地，增益=1+Rf/Rg。 | — | [non-inverting-amplifier.json](./non-inverting-amplifier.json) |
| `npn-low-side-switch` | NPN 低边开关 | NPN 三极管集电极接负载，发射极接地，基极通过电阻接控制信号。 | role | [npn-low-side-switch.json](./npn-low-side-switch.json) |
| `parallel-termination` | 并联终端 | 多个电阻并联于信号线与地之间，常见于终端匹配、下拉阵列或 ESD 保护。 | — | [parallel-termination.json](./parallel-termination.json) |
| `pmos-high-side-switch` | PMOS 高边开关 | PMOS 源极接电源，漏极接负载，栅极接控制信号（通常需拉低导通）。 | role | [pmos-high-side-switch.json](./pmos-high-side-switch.json) |
| `pull-down-resistor` | 下拉电阻 | 电阻一端接地（role=gnd），另一端接数字信号（role=digital），将信号默认拉到低电平。 | role | [pull-down-resistor.json](./pull-down-resistor.json) |
| `pull-up-resistor` | 上拉电阻 | 电阻一端接电源（role=power），另一端接数字信号（role=digital），将信号默认拉到高电平。 | role | [pull-up-resistor.json](./pull-up-resistor.json) |
| `rc-high-pass` | RC 高通滤波器 | 电容串联、电阻从输出节点到地：高频通过、低频/直流被电容阻断。 | — | [rc-high-pass.json](./rc-high-pass.json) |
| `rc-low-pass` | RC 低通滤波器 | 电阻串联、电容从输出节点到地：低频通过、高频被电容旁路。 | — | [rc-low-pass.json](./rc-low-pass.json) |
| `sallen-key-low-pass` | Sallen-Key 二阶低通滤波器 | 运放 + 2R + 2C 构成的单位增益二阶有源低通。 | role | [sallen-key-low-pass.json](./sallen-key-low-pass.json) |
| `tvs-protection` | TVS 保护二极管 | TVS 二极管跨接在被保护信号/电源与地之间，过压时击穿钳位。 | role | [tvs-protection.json](./tvs-protection.json) |
| `type-c-cc-pull-down` | Type-C CC Pull-down | USB Type-C CC pin pulled to ground through a 5.1 kΩ resisto… | — | [type-c-cc-pull-down.json](./type-c-cc-pull-down.json) |
| `voltage-divider` | 电压分压器 | 两电阻串联构成的分压网络：上电阻连接输入与输出，下电阻连接输出与地。 | — | [voltage-divider.json](./voltage-divider.json) |

## 新增基元

在 `primitives/` 目录新增一个 `<id>.json`，pre-commit hook 会自动重生成此文件。
若未安装 hook，可手动跑 `uv run python scripts/build_primitive_index.py`。
