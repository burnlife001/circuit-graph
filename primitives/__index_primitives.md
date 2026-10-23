# Primitives 索引

> 由 [`scripts/build_primitive_index.py`](./scripts/build_primitive_index.py) 自动生成于 2026-10-23，共 27 个基元。请勿手改。

| ID | 名称 | 用途 | 严格模式 | 文件 |
|---|---|---|---|---|
| `bootstrap-cap` | 自举电容 | 自举电容接在开关节点与 BST 引脚之间（100nF 典型），为高压侧栅极驱动提供浮地电源。 | — | [power/bootstrap-cap.json](./power/bootstrap-cap.json) |
| `bridge-rectifier` | 桥式整流器 | 4 个二极管组成全波整流桥：2 个 AC 输入，1 个 DC+ 输出，1 个 DC- 输出（通常接地）。 | — | [power/bridge-rectifier.json](./power/bridge-rectifier.json) |
| `bridge-rectifier-package` | 桥堆整流器（封装确认） | 封装已确认的集成桥堆（footprint 含 BRIDGE，如 GBJ1510/MB10S）：4 引脚器件跨接 2 路… | — | [power/bridge-rectifier-package.json](./power/bridge-rectifier-package.json) |
| `crystal-with-load-caps` | 晶振 + 负载电容 | 晶体 / 陶瓷谐振器两端各接一个负载电容到地（Pierce 振荡器拓扑）。 | — | [timing/crystal-with-load-caps.json](./timing/crystal-with-load-caps.json) |
| `current-shunt-monitor` | 电流采样电阻 | 小阻值精密电阻串联在电源路径中，两端压降反映电流；常配合电流检测放大器使用。 | role | [power/current-shunt-monitor.json](./power/current-shunt-monitor.json) |
| `differential-comparator` | 差分比较器 | 运放无反馈、差分输入：同相端/反相端直连差分信号，输出饱和至 VCC 或 GND；常用于 RF 信号电平判定、差分→单… | — | [amplifier/differential-comparator.json](./amplifier/differential-comparator.json) |
| `fet-level-shifter` | FET 双向电平转换 | NMOS + 两个上拉电阻实现双向电平转换（常见于 I2C 5V↔3V3）。 | level, role | [interface/fet-level-shifter.json](./interface/fet-level-shifter.json) |
| `flyback-diode` | 续流二极管 | 二极管反向并联在电感/继电器线圈两端，关断时为感应电流提供续流路径。 | role | [protection/flyback-diode.json](./protection/flyback-diode.json) |
| `integrated-bridge-rectifier` | 集成桥堆整流器 | 单颗集成桥堆实现全波整流：一个 4 引脚器件同时跨接 2 路交流输入（L/N/AC*）与正负直流母线。不依赖位号命名（… | — | [power/integrated-bridge-rectifier.json](./power/integrated-bridge-rectifier.json) |
| `inverting-amplifier` | 反向比例放大器 | 运放反向比例放大：输入信号经Rin接入反相端(-)，反馈电阻Rf跨接输出与反相端，同相端(+)接地，增益=-Rf/Ri… | — | [amplifier/inverting-amplifier.json](./amplifier/inverting-amplifier.json) |
| `lc-pi-filter` | π 型 LC 滤波器 | C-L-C π 型滤波：输入电容 + 串联电感 + 输出电容，用于电源纹波抑制。 | role | [filter/lc-pi-filter.json](./filter/lc-pi-filter.json) |
| `nmos-low-side-switch` | NMOS 低边开关 | NMOS 漏极接负载，源极接地，栅极接控制信号。 | role | [switch/nmos-low-side-switch.json](./switch/nmos-low-side-switch.json) |
| `npn-low-side-switch` | NPN 低边开关 | NPN 三极管集电极接负载，发射极接地，基极通过电阻接控制信号。 | role | [switch/npn-low-side-switch.json](./switch/npn-low-side-switch.json) |
| `opamp-unit-differential-amplifier` | 运放单元-差分放大器 | 运放单元差分放大：R1/Rin1将VIN1接入反相端，R2/Rin2将VIN2接入同相端，Rf跨接输出与反相端，Rg连… | — | [amplifier/opamp-unit-differential-amplifier.json](./amplifier/opamp-unit-differential-amplifier.json) |
| `opamp-unit-voltage-follower` | 运放单元-电压跟随器 | 运放单元 unity-gain buffer：反相端(-)与输出端(OUT)短接作为反馈网络，同相端(+)接输入；电压… | — | [amplifier/opamp-unit-voltage-follower.json](./amplifier/opamp-unit-voltage-follower.json) |
| `parallel-termination` | 并联终端 | 多个电阻并联于信号线与地之间，常见于终端匹配、下拉阵列或 ESD 保护。 | — | [termination/parallel-termination.json](./termination/parallel-termination.json) |
| `peak-envelope-detector` | 峰值包络检波采样电路 | 开关二极管对开关节点/高频方波整流，经串联限流电阻对保持电容充电形成峰值包络；并由上拉/泄放电阻构成放电回路，输出送往… | — | [power/peak-envelope-detector.json](./power/peak-envelope-detector.json) |
| `pmos-high-side-switch` | PMOS 高边开关 | PMOS 源极接电源，漏极接负载，栅极接控制信号（通常需拉低导通）。 | role | [switch/pmos-high-side-switch.json](./switch/pmos-high-side-switch.json) |
| `pull-down-resistor` | 下拉电阻 | 电阻一端接地（role=gnd），另一端接数字信号（role=digital），将信号默认拉到低电平。 | role | [termination/pull-down-resistor.json](./termination/pull-down-resistor.json) |
| `pull-up-resistor` | 上拉电阻 | 电阻一端接电源（role=power），另一端接数字信号（role=digital），将信号默认拉到高电平。 | role | [termination/pull-up-resistor.json](./termination/pull-up-resistor.json) |
| `rc-high-pass` | RC 高通滤波器 | 电容串联、电阻从输出节点到地：高频通过、低频/直流被电容阻断。 | — | [filter/rc-high-pass.json](./filter/rc-high-pass.json) |
| `rc-low-pass` | RC 低通滤波器 | 电阻串联、电容从输出节点到地：低频通过、高频被电容旁路。 | — | [filter/rc-low-pass.json](./filter/rc-low-pass.json) |
| `rc-snubber` | RC 吸收电路 | 开关节点 RC 吸收：电阻从开关节点串联到中间网，电容从中间网到地（2Ω + 4.7nF 典型），抑制开关振铃。 | role | [protection/rc-snubber.json](./protection/rc-snubber.json) |
| `sallen-key-low-pass` | Sallen-Key 二阶RC低通滤波器 | 运放电压跟随器 + 2R + 2C 构成的单位增益二阶有源低通：C1、C2 均接 Vout 形成正反馈，决定截止频率与… | role | [amplifier/sallen-key-low-pass.json](./amplifier/sallen-key-low-pass.json) |
| `tvs-protection` | TVS 保护二极管 | TVS 二极管跨接在被保护信号/电源与地之间，过压时击穿钳位。 | role | [protection/tvs-protection.json](./protection/tvs-protection.json) |
| `type-c-cc-pull-down` | Type-C CC Pull-down | USB Type-C CC pin pulled to ground through a 5.1 kΩ resisto… | — | [termination/type-c-cc-pull-down.json](./termination/type-c-cc-pull-down.json) |
| `voltage-divider` | 电压分压器 | 两电阻串联构成的分压网络：上电阻连接输入与输出，下电阻连接输出与地。 | — | [power/voltage-divider.json](./power/voltage-divider.json) |

## 新增基元

在 `primitives/` 下选择合适的分类子目录（amplifier / filter / power / switch / protection / termination / interface / timing）新增 `<id>.json`，pre-commit hook 会自动重生成此文件。
若未安装 hook，可手动跑 `uv run python scripts/build_primitive_index.py`。
