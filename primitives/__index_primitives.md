# Primitives 索引

> 由 [`scripts/build_primitive_index.py`](../scripts/build_primitive_index.py) 自动生成，共 53 个基元。请勿手改。

| ID | 名称 | 用途 | 严格模式 | 文件 |
|---|---|---|---|---|
| `auto-rh6016c` | RH6016C 典型应用（自动） | 自动模板：参考板 FX-T268-V6-10米-5065.NET 的 U3（RH6016C）及其同网无源件；引脚 1=… | role | [auto/rh6016c.json](./auto/rh6016c.json) |
| `bjt-npn-low-side-switch` | NPN 三极管低边开关 | NPN 双极管（BJT）低边开关：发射极接地，集电极接负载，基极经限流电阻接控制信号。注意：IR 的 DeviceKi… | role | [transistor/bjt-npn-low-side-switch.json](./transistor/bjt-npn-low-side-switch.json) |
| `bootstrap-cap` | 自举电容 | 自举电容接在开关节点与 BST 引脚之间（100nF 典型），为高压侧栅极驱动提供浮地电源。 | — | [power/bootstrap-cap.json](./power/bootstrap-cap.json) |
| `bridge-rectifier` | 桥式整流器 | 4 个二极管组成全波整流桥：2 个 AC 输入，1 个 DC+ 输出，1 个 DC- 输出（通常接地）。 | — | [power/bridge-rectifier.json](./power/bridge-rectifier.json) |
| `bridge-rectifier-package` | 桥堆整流器（封装确认） | 封装已确认的集成桥堆（footprint 含 BRIDGE，如 GBJ1510/MB10S）：4 引脚器件跨接 2 路… | — | [power/bridge-rectifier-package.json](./power/bridge-rectifier-package.json) |
| `buck-converter` | 同步降压变换器 | 同步降压变换器 IC：VIN 输入，SW 经电感/电容滤波输出，FB 做输出电压反馈，GND 接地，BST 为高侧驱动… | — | [power/buck-converter.json](./power/buck-converter.json) |
| `buck-input-cap` | Buck 输入滤波电容 | 降压变换器 VIN 引脚（KB 认定为 power_in）到地之间的大容量输入滤波电容，抑制输入轨上的开关噪声。反例约… | role | [power/buck-input-cap.json](./power/buck-input-cap.json) |
| `cc-detect-series-resistor` | CCx 检测串联电阻 | Type-C CC 控制器与 MCU 之间 CCx 检测线的串接电阻：MCU 通过 CC1ID/CC2ID 回读 CC… | — | [interface/cc-detect-series-resistor.json](./interface/cc-detect-series-resistor.json) |
| `cc-line-filter-cap` | CC 线高频滤波电容 | Type-C 的 CC1/CC2 线对 PGND 的 pF 级高频滤波电容：CC 线上跑的是 PD 握手脉冲，pF 级… | role | [interface/cc-line-filter-cap.json](./interface/cc-line-filter-cap.json) |
| `cc-send-series-resistor` | CCx 发送串联电阻 | Type-C CC 控制器与 MCU 之间 CCx 发送线的串接电阻：MCU 通过 CC1IO/CC2IO 驱动 CC… | — | [interface/cc-send-series-resistor.json](./interface/cc-send-series-resistor.json) |
| `crystal-with-load-caps` | 晶振 + 负载电容 | 晶体 / 陶瓷谐振器两端各接一个负载电容到地（Pierce 振荡器拓扑）。 | — | [timing/crystal-with-load-caps.json](./timing/crystal-with-load-caps.json) |
| `current-shunt-monitor` | 电流采样电阻 | 小阻值精密电阻串联在电源路径中，两端压降反映电流；常配合电流检测放大器使用。 | role | [passive/current-shunt-monitor.json](./passive/current-shunt-monitor.json) |
| `dc-decoupling-cap` | DC 电源去耦电容 | 电容跨接在直流电源轨（DCIN/VIN/VBUS 等）与地（GND/CGND）之间，滤除电源线上的噪声与纹波。μF 级… | role | [passive/dc-decoupling-cap.json](./passive/dc-decoupling-cap.json) |
| `detector-amp-sampling` | 二极管检波带通 ADC 采样 | 二极管对开关节点信号整流，经 RC 低通（保持）和 RC 高通（耦合）构成带通滤波，配合分压偏置提供 MCU ADC … | — | [sensor/detector-amp-sampling.json](./sensor/detector-amp-sampling.json) |
| `differential-comparator` | 差分比较器 | 运放无反馈、差分输入：同相端/反相端直连差分信号，输出饱和至 VCC 或 GND；常用于 RF 信号电平判定、差分→单… | — | [interface/differential-comparator.json](./interface/differential-comparator.json) |
| `feedback-feedforward-cap` | 反馈分压馈通补偿电容 | 开关电源输出轨经一颗 pF 级小电容馈通到反馈分压节点（FB）：给环路加一条高频旁路，加速环路响应。本模板只认「输出轨… | role | [power/feedback-feedforward-cap.json](./power/feedback-feedforward-cap.json) |
| `fet-level-shifter` | FET 双向电平转换 | NMOS + 两个上拉电阻实现双向电平转换（常见于 I2C 5V↔3V3）。 | level, role | [interface/fet-level-shifter.json](./interface/fet-level-shifter.json) |
| `flyback-diode` | 续流二极管 | 二极管反向并联在电感/继电器线圈两端，关断时为感应电流提供续流路径。 | role | [protection/flyback-diode.json](./protection/flyback-diode.json) |
| `gate-pull-down-to-source` | MOSFET 栅极下拉电阻（拉到源公共端） | 功率级 MOSFET 的栅极经一颗 10k 级电阻拉到源/公共端（不是地）：驱动失效或 MCU 复位时把栅压拉回关断电… | role | [transistor/gate-pull-down-to-source.json](./transistor/gate-pull-down-to-source.json) |
| `ic-pin-filter-cap` | IC 引脚滤波电容 | IC 的模拟/检测类引脚（VOL/NTC/TEMP/VSENSE/ADJ…）经一颗 100nF 级电容对地滤波，滤掉引… | role | [passive/ic-pin-filter-cap.json](./passive/ic-pin-filter-cap.json) |
| `integrated-bridge-rectifier` | 集成桥堆整流器 | 单颗集成桥堆实现全波整流：一个 4 引脚器件同时跨接 2 路交流输入（L/N/AC*）与正负直流母线。不依赖位号命名（… | — | [power/integrated-bridge-rectifier.json](./power/integrated-bridge-rectifier.json) |
| `inverting-amplifier` | 反向比例放大器 | 运放反向比例放大：输入信号经Rin接入反相端(-)，反馈电阻Rf跨接输出与反相端，同相端(+)接地，增益=-Rf/Ri… | — | [amplifier/inverting-amplifier.json](./amplifier/inverting-amplifier.json) |
| `jw5065-buck` | JW5065 同步降压变换器 | Joulwatt JW5065：24V/3A 同步降压变换器（I2 架构，450kHz，SOT23-8）。VIN 经内… | — | [power/jw5065-buck.json](./power/jw5065-buck.json) |
| `lc-pi-filter` | π 型 LC 滤波器 | C-L-C π 型滤波：输入电容 + 串联电感 + 输出电容，用于电源纹波抑制。 | role | [filter/lc-pi-filter.json](./filter/lc-pi-filter.json) |
| `led-driver-switch` | LED 驱动低边开关 | NPN 三极管发射极接地、集电极接负载，基极经限流电阻接 MCU 数字信号。控制信号为高电平时三极管导通，负载（LED… | — | [transistor/led-driver-switch.json](./transistor/led-driver-switch.json) |
| `level-shift-clamp` | CC 线电平转换/钳位电路 | 2N7002 小信号 MOSFET 做 Type-C CC 线的电平转换/钳位：漏极接连接器侧 CC 脚、源极接控制器… | role | [interface/level-shift-clamp.json](./interface/level-shift-clamp.json) |
| `nmos-low-side-switch` | NMOS 低边开关 | NMOS 漏极接负载，源极接地，栅极接控制信号。 | role | [transistor/nmos-low-side-switch.json](./transistor/nmos-low-side-switch.json) |
| `npn-low-side-switch` | NPN 低边开关 | NPN 三极管集电极接负载，发射极接地，基极通过电阻接控制信号。IR 的 DeviceKind 没有 BJT，未知器件… | role | [transistor/npn-low-side-switch.json](./transistor/npn-low-side-switch.json) |
| `opamp-noninv-feedback` | 同相反馈对（负反馈放大器） | 同相放大器的反馈电阻对：Rf 跨运放输出与反相端，Rg 反相端到地；同相增益 = 1 + Rf/Rg。 | — | [amplifier/opamp-noninv-feedback.json](./amplifier/opamp-noninv-feedback.json) |
| `opamp-unit-differential-amplifier` | 运放单元-差分放大器 | 运放单元差分放大：R1/Rin1将VIN1接入反相端，R2/Rin2将VIN2接入同相端，Rf跨接输出与反相端，Rg连… | — | [amplifier/opamp-unit-differential-amplifier.json](./amplifier/opamp-unit-differential-amplifier.json) |
| `opamp-unit-voltage-follower` | 运放单元-电压跟随器 | 运放单元 unity-gain buffer：反相端(-)与输出端(OUT)短接作为反馈网络，同相端(+)接输入；电压… | — | [amplifier/opamp-unit-voltage-follower.json](./amplifier/opamp-unit-voltage-follower.json) |
| `parallel-termination` | 并联终端 | 多个电阻并联于信号线与地之间，常见于终端匹配、下拉阵列或 ESD 保护。 | — | [termination/parallel-termination.json](./termination/parallel-termination.json) |
| `peak-envelope-detector` | 峰值包络检波采样电路 | 开关二极管对开关节点/高频方波整流，经串联限流电阻对保持电容充电形成峰值包络；并由上拉/泄放电阻构成放电回路，输出送往… | — | [sensor/peak-envelope-detector.json](./sensor/peak-envelope-detector.json) |
| `pmos-high-side-switch` | PMOS 高边开关 | PMOS 源极接电源，漏极接负载，栅极接控制信号（通常需拉低导通）。 | role | [transistor/pmos-high-side-switch.json](./transistor/pmos-high-side-switch.json) |
| `pull-down-resistor` | 下拉电阻 | 电阻一端接地（role=gnd），另一端接数字信号（role=digital），将信号默认拉到低电平。 | — | [passive/pull-down-resistor.json](./passive/pull-down-resistor.json) |
| `pull-up-resistor` | 上拉电阻 | 电阻一端接电源（role=power），另一端接数字信号（role=digital），将信号默认拉到高电平。 | role | [passive/pull-up-resistor.json](./passive/pull-up-resistor.json) |
| `pwm-gate-series-resistor` | PWM 栅极驱动串联电阻 | MCU 的 PWM 输出经一颗小阻值电阻（几欧到几十欧）串到功率 MOSFET 的栅极：既做驱动阻抗匹配，也给栅极电容… | role | [transistor/pwm-gate-series-resistor.json](./transistor/pwm-gate-series-resistor.json) |
| `qc-control-resistor` | QC 协议 D+/D- 控制电阻 | USB 数据线 D+/D- 与 MCU GPIO 之间的控制电阻：快充协议（QC/AFC/VOOC…）靠 MCU 在 … | role | [interface/qc-control-resistor.json](./interface/qc-control-resistor.json) |
| `rc-high-pass` | RC 高通滤波器 | 电容串联、电阻从输出节点到地：高频通过、低频/直流被电容阻断。 | — | [filter/rc-high-pass.json](./filter/rc-high-pass.json) |
| `rc-low-pass` | RC 低通滤波器 | 电阻串联、电容从输出节点到地：低频通过、高频被电容旁路。vin（电阻另一端）与 vout（滤波节点）都排除电源轨 ——… | — | [filter/rc-low-pass.json](./filter/rc-low-pass.json) |
| `rc-reset-network` | MCU 上电复位 RC 网络 | MCU 上电复位电路：上拉电阻把复位脚拉到电源，电容从复位脚接到地，上电瞬间靠 RC 延时完成复位。 | role | [timing/rc-reset-network.json](./timing/rc-reset-network.json) |
| `rc-snubber` | RC 吸收电路 | 开关节点 RC 吸收：电阻从开关节点串联到中间网，电容从中间网到地（2Ω + 4.7nF 典型），抑制开关振铃。 | role | [protection/rc-snubber.json](./protection/rc-snubber.json) |
| `rc-snubber-series` | RC 吸收网络（R+C 串联到地） | 开关节点经小阻值电阻串联小容量电容到地的 snubber：R 限制充放电峰值电流并阻尼谐振，C 吸收高频振铃，常用于 … | role | [protection/rc-snubber-series.json](./protection/rc-snubber-series.json) |
| `rc-snubber-shunt` | RC 吸收网络（单 C 到地） | 开关节点直接经小容量电容到地的 snubber：吸收 MOSFET 开关瞬间的高频振铃与 dv/dt 毛刺，降低 EM… | role | [protection/rc-snubber-shunt.json](./protection/rc-snubber-shunt.json) |
| `resonant-commutation-cap` | 谐振/换流电容 | 跨接在两个开关/谐振节点之间的大容量电容（µF 级），不到地，参与 LLC/LC 谐振换流、储存并转移谐振能量。 | — | [power/resonant-commutation-cap.json](./power/resonant-commutation-cap.json) |
| `sallen-key-low-pass` | Sallen-Key 二阶RC低通滤波器 | 运放电压跟随器 + 2R + 2C 构成的单位增益二阶有源低通：C1、C2 均接 Vout 形成正反馈，决定截止频率与… | role | [filter/sallen-key-low-pass.json](./filter/sallen-key-low-pass.json) |
| `serial-line-current-limit` | 串口线限流保护电阻 | 限流保护容错电阻：串在 MCU 串口线（RXD/TXD）与连接器之间，限制总线电流，并对误接/静电冲击做容错保护。 | role | [protection/serial-line-current-limit.json](./protection/serial-line-current-limit.json) |
| `sw5001-full-bridge` | SW5001 全桥无线充发射功率级 | 珠海智融 SW5001：集成输入快充的 15W 无线充发射端 Power Stage 芯片。芯片内置 4 管 NMOS… | — | [power/sw5001-full-bridge.json](./power/sw5001-full-bridge.json) |
| `tvs-protection` | TVS 保护二极管 | TVS 二极管跨接在被保护信号/电源与地之间，过压时击穿钳位。 | role | [protection/tvs-protection.json](./protection/tvs-protection.json) |
| `type-c-cc-pull-down` | Type-C CC Pull-down | USB Type-C CC pin pulled to ground through a 5.1 kΩ resisto… | — | [interface/type-c-cc-pull-down.json](./interface/type-c-cc-pull-down.json) |
| `vbus-input-filter-cap` | Type-C VBUS 输入滤波电容 | Type-C 连接器的 VBUS 引脚经一颗 10nF 级电容对地（PGND）滤波：VBUS 直接落到连接器侧，噪声先… | role | [interface/vbus-input-filter-cap.json](./interface/vbus-input-filter-cap.json) |
| `voltage-divider` | 电压分压器 | 两电阻串联构成的分压网络：上电阻连接输入与输出，下电阻连接输出与地。vin 必须是电源轨（expected_role=… | role | [passive/voltage-divider.json](./passive/voltage-divider.json) |
| `voltage-divider-signal` | 信号分压器（送 IC 采样） | 信号源节点 → 上电阻 → 中点 → 下电阻 → 地，中点接到 IC 引脚（典型是 MCU ADC 采样脚）。与 vo… | role | [interface/voltage-divider-signal.json](./interface/voltage-divider-signal.json) |

## 新增基元

在 `primitives/` 下选择合适的分类子目录（amplifier / filter / interface / passive / power / protection / sensor / termination / timing / transistor）新增 `<id>.json`，pre-commit hook 会自动重生成此文件。
若未安装 hook，可手动跑 `uv run python scripts/build_primitive_index.py`。
