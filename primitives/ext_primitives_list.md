# 可扩展 Primitives 清单

> 当前已落地 22 个（见 [`__index_primitives.md`](./__index_primitives.md)）。本文件盘点还可补充的拓扑基元，按"识别价值 / 实现成本"排序。
>
> 字段依赖沿用现有 3 组分类：
> - **组 1（纯结构）**：仅依赖拓扑 + `name_pattern`，无需 `expected_*`
> - **组 2（role 依赖）**：至少一个 signal 需 `expected_role`
> - **组 3（role+direction+level）**：至少一个 signal 需 `expected_direction` 或 `expected_level`
>
> 每条给出：建议 ID / 用途一句话 / 关键 components / 关键 signals / 严格模式字段 / 优先级。

---

## A. 电源 / 稳压 / 去耦（fixture 高频，优先做）

| # | 建议 ID | 名称 | 用途 | 关键 components | 关键 signals | 严格字段 | 优先级 |
|---|---|---|---|---|---|---|---|
| A4 | `buck-converter` | 同步/异步降压 | 控制器/集成功率级 + 电感 + 输出电容 + 反馈分压 | U(buck), L, C_out, R_fb1, R_fb2 | VIN, SW, VOUT, FB, GND | role+level | ★★★ |
| A5 | `boost-converter` | 升压变换器 | 控制器 + 电感 + 整流管/同步管 + 输出电容 + 反馈分压 | U(boost), L, D/Q, C_out, R_fb×2 | VIN, SW, VOUT, FB | role+level | ★★ |
| A6 | `feedback-divider` | 稳压反馈分压 | `voltage-divider` 的电源版：上/下电阻分压点接 FB 引脚 | R_top, R_bot | VOUT, FB, GND | role | ★★★ |
| A7 | `ferrite-bead-filter` | 磁珠 + 电容滤波 | FB 串电源，两侧各一电容到地（π 型变体） | L(ferrite), C_in, C_out | VIN, VOUT, GND | role | ★★ |
| A8 | `rc-snubber` | RC 吸收/缓冲 | R+C 串联后跨在 SW 与 GND（或开关管 D-S）之间 | R, C | SW, GND | role | ★★ |
| A9 | `zener-shunt-reference` | 稳压二极管/电压基准 | 限流电阻 + 齐纳管到地，输出接负载/基准 | R, D(zener) | VIN, VREF, GND | role | ★★ |
| A10 | `tl431-shunt-regulator` | TL431 并联稳压 | TL431 + 上分压 + 阴极串联限流 | U(TL431), R_bias, R_top, R_bot | VIN, VOUT, REF, GND | role+level | ★ |

## B. 开关 / 驱动 / 保护

| # | 建议 ID | 名称 | 用途 | 关键 components | 关键 signals | 严格字段 | 优先级 |
|---|---|---|---|---|---|---|---|
| B1 | `pnp-high-side-switch` | PNP 高边开关 | PNP 发射极接电源、集电极接负载、基极经电阻受控 | Q(pnp), R_base | VCC, LOAD, CTRL | role | ★★★ |
| B2 | `relay-driver` | 继电器/线圈驱动 | NPN/NMOS 低边 + 续流二极管跨线圈 | Q, D, (R_base) | VCC, COIL, CTRL, GND | role | ★★ |
| B3 | `led-series-resistor` | LED + 限流电阻 | 电源→电阻→LED→地（或反向） | R, LED | VCC/CTRL, GND | role | ★★★ |
| B4 | `reverse-polarity-pmos` | PMOS 防反接 | PMOS 串在 VIN 路径，栅极接地、体二极管朝负载 | Q(pmos) | VIN, VOUT, GND | role | ★★ |
| B5 | `reverse-polarity-diode` | 串联二极管防反 | 二极管串在 VIN 路径 | D | VIN, VOUT | role | ★ |
| B6 | `half-bridge` | 半桥功率级 | 高边 + 低边 MOSFET，中点 SW | Q_hi, Q_lo | VBUS, SW, GND, HI/LO gate | role | ★ |
| B7 | `push-pull-output` | 推挽输出级 | NPN+PNP（或 N+P MOS）图腾柱 | Q_n, Q_p | VCC, OUT, GND, CTRL | role | ★ |
| B8 | `esd-diode-to-rails` | 双向 ESD 钳位 | 二极管把信号钳到 VCC / GND（轨到轨） | D_hi, D_lo | SIG, VCC, GND | role | ★★ |

## C. 信号调理 / 运放拓扑

| # | 建议 ID | 名称 | 用途 | 关键 components | 关键 signals | 严格字段 | 优先级 |
|---|---|---|---|---|---|---|---|
| C1 | `voltage-follower` | 电压跟随器 | 运放 OUT 直连 IN-，信号进 IN+ | U(opamp) | IN, OUT, VCC, GND | role | ★★★ |
| C2 | `comparator-hysteresis` | 迟滞比较器 | 运放/比较器 + 正反馈电阻 + 阈值分压 | U(cmp), R_fb, R_ref×2 | IN, OUT, REF | role | ★★ |
| C3 | `summing-amplifier` | 反相求和放大 | 多路输入各经电阻汇到反相端，Rf 反馈 | U(opamp), R_in×N, R_f | IN1..N, OUT | role | ★ |
| C4 | `instrumentation-amp` | 三运放仪表放大器 | 两缓冲 + 差分级 + R_gain | U×2..3, R_g, R×4 | IN+, IN-, OUT, REF | role | ★ |
| C5 | `transimpedance-amp` | 跨阻放大器 | 光电/电流源进反相端，Rf（‖Cf）反馈 | U(opamp), R_f, (C_f), PD | IN, OUT | role | ★ |
| C6 | `multiple-feedback-lp` | MFB 二阶低通 | 反相拓扑：R1-R2 串、C1 到地、C2 反馈 | U(opamp), R×2, C×2 | IN, OUT | role | ★ |
| C7 | `sallen-key-high-pass` | Sallen-Key 高通 | 同相拓扑，R/C 位置与低通互换 | U(opamp), R×2, C×2 | IN, OUT | role | ★ |
| C8 | `precision-half-wave-rectifier` | 精密半波整流 | 运放 + 二极管反馈环 | U(opamp), D, R | IN, OUT | role | ★ |
| C9 | `rc-differentiator` | RC 微分器 | 与 rc-high-pass 同构，区别在用途命名（可选合并） | R, C | IN, OUT | — | ★ |

## D. 接口 / 总线 / 端接

| # | 建议 ID | 名称 | 用途 | 关键 components | 关键 signals | 严格字段 | 优先级 |
|---|---|---|---|---|---|---|---|
| D2 | `series-termination` | 串联端接 | 电阻串在驱动源与走线之间 | R | SRC, DST | — | ★★ |
| D3 | `differential-termination` | 差分对端接 | 电阻跨接差分线（100Ω/120Ω） | R | DP, DM | — | ★★ |
| D4 | `can-termination-split` | CAN 分裂终端 | 两颗 60Ω 串联 + 中点电容到地 | R×2, C | CANH, CANL, GND | role | ★ |
| D5 | `rs485-bias-network` | RS485 偏置 | 上拉 A、下拉 B、120Ω 终端 | R_pu, R_pd, R_term | A, B, VCC, GND | role | ★ |
| D6 | `mos-level-converter` | mos电平转换 |  |  |  |  | ★★ |

## E. 时钟 / 复位 / 启动

| # | 建议 ID | 名称 | 用途 | 关键 components | 关键 signals | 严格字段 | 优先级 |
|---|---|---|---|---|---|---|---|
| E1 | `pierce-oscillator` | Pierce 振荡器 | 反相器/运放 + 晶振 + 反馈 R + 两负载 C | U/X, R_f, C×2 | XI, XO, GND | role | ★★ |
| E2 | `rc-power-on-reset` | RC 上电复位 | VCC→R→RST 节点→C→GND，可选并联二极管加速放电 | R, C, (D) | VCC, RST, GND | role | ★★★ |
| E3 | `reset-button-pullup` | 复位按键 + 上拉 | 按键接地，RST 节点上拉 + 可选去抖 C | SW, R_pu, (C) | RST, VCC, GND | role | ★★ |
| E4 | `astable-555` | 555 无稳态 | 555 + R_A/R_B/C 定时网络 | U(555), R×2, C | VCC, OUT, TRIG/THRES, GND | role | ★ |
| E5 | `mcu-boot-cfg` | BOOT 配置电阻 | 上拉/下拉固定 BOOT 引脚电平（可合并到 pull-up/down） | R | BOOT, VCC/GND | role | ★ |

## F. 传感器 / 测量

| # | 建议 ID | 名称 | 用途 | 关键 components | 关键 signals | 严格字段 | 优先级 |
|---|---|---|---|---|---|---|---|
| F1 | `ntc-divider` | NTC 分压测温 | 固定电阻 + NTC 分压，中点接 ADC | R, RT(ntc) | VCC, ADC, GND | role | ★★ |
| F2 | `wheatstone-bridge` | 惠斯通电桥 | 四电阻桥 + 差分输出 | R×4 | EXC+, EXC-, OUT+, OUT- | role | ★ |
| F3 | `photo-diode-amp` | 光电二极管前端 | PD 反向接跨阻放大（同 C5，可选合并） | PD, U(opamp), R_f | OUT | role | ★ |

## G. EMI / 安规（低频但易识别）

| # | 建议 ID | 名称 | 用途 | 关键 components | 关键 signals | 严格字段 | 优先级 |
|---|---|---|---|---|---|---|---|
| G1 | `common-mode-choke-filter` | 共模扼流圈 + Y 电容 | CMC 串在差分/电源对，两侧 Y 电容到机壳地 | L(cmc), C×2 | IN±, OUT±, PE | role | ★ |
| G2 | `y-cap-across-isolation` | 跨隔离 Y 电容 | 单电容连接初级地与次级地 | C | GND_PRI, GND_SEC | role | ★ |
| G3 | `bleed-resistor` | 泄放电阻 | 大阻值电阻跨高压电容/母线 | R | HV+, HV- | role | ★ |
| G4 | `inrush-ntc` | NTC 浪涌抑制 | NTC 串在 AC/DC 输入 | RT | IN, OUT | — | ★ |

---

## 落盘流程

新增文件 `primitives/<id>.json` 后：
- 若已装 pre-commit hook：自动重生成 `__index_primitives.md`
- 否则手动：`uv run python scripts/build_primitive_index.py`

字段细节以 [`schema.json`](./schema.json) 为准；`expected_*` 触发严格模式，声明后缺 `edge_semantic` 会**直接识别为零**（无报错、无结果）。
