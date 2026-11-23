# PRD：KB 引脚类别补录 —— 解除电路识别器的盲区

创建：2026-11-03
状态：**待评审（未提交）**
前置提交：`f5096e7`（T193 有效对齐率 100%）
数据来源：2026-11-03 全仓 15 块板 / 1240 个元件实测
相关文档：`docs/template-system-assets.md`（资产与三件套）、`docs/template-system-next-plan.md`（上一阶段计划）

---

## 一、结论先行

**当前的瓶颈不是模板不够，是 KB 里没有可用的引脚类别。**

全仓 52 种主芯片型号里，只有 **7 种（13%）** 能被识别器解析出引脚类别（power_in / power_out / ground / input / output …）。
其余 45 种要么 KB 里根本没有这个型号（21 种），要么型号在但引脚名映射不到类别（24 种）。

直接后果：识别器只能靠拓扑猜，遇到"这个脚到底是电源脚还是信号脚"就放弃。
盲区电容里 **127/255（50%）** 正是因为"KB 解析不出对端 IC 的引脚类别"。

**因此本 PRD 的主线是 KB 补录，不是补模板。** 模板侧只剩 20 个电容值得用"IC 电源脚旁证"去捞（见 §四·B），属于第二批。

---

## 二、现状数据

### 2.1 逐板覆盖（`cg role --all` + `cg primitives coverage`，2026-11-03）

| 板卡 | 元件 | 命中模板块 | cg 完全答不出（fallback/unconnected） | 基准层 |
|---|---:|---:|---:|---|
| T255-3.0 | 342 | 92 | **95** | 无 |
| smoke-main-V05 | 119 | 35 | **58** | 无 |
| 500W-TX-Main-Zero | 123 | 53 | **53** | 无 |
| M4-V8.03 | 84 | 19 | **47** | 无 |
| 500W-TX-Main | 102 | 47 | **42** | 无 |
| 500W-RX | 60 | 26 | **27** | 无 |
| FX-T268-V6-10米-5065 | 207 | 124 | **24** | 有（有效对齐 100%） |
| TX_Display-HT(V1.1) | 31 | 9 | **22** | 无 |
| add-primitives | 31 | 14 | **16** | 无 |
| 无码接收 | 36 | 19 | **13** | 无 |
| 433_RX | 14 | 4 | **8** | 无 |
| 遥控器2 | 12 | 0 | **5** | 无 |
| T193 | 54 | 21 | **1** | 有（有效对齐 100%） |
| extra-primitives | 16 | 9 | **5** | 无 |
| current-shunt | 2 | 2 | **0** | 无 |
| **合计** | **1240** | **493** | **416** | 2/15 块板 |

> 口径说明：「命中模板块」= `cg primitives coverage` 的 matched；「cg 完全答不出」= `cg role --all` 里 rule 为
> `fallback`/`unconnected` 的元件。两者不等价 —— 大量元件没进模板块，但被泛化规则（rc-low-pass / series_signal /
> coupling / test_point…）兜住了。

### 2.2 盲区元件构成（未进任何模板块的 788 个）

| 类型 | 数量 | | 类型 | 数量 |
|---|---:|---|---|---:|
| Resistor | 228 | | Connector | 42 |
| Capacitor | 255 | | Inductor | 30 |
| Unknown | 89 | | Mosfet | 9 |
| Diode | 71 | | IC | 63 |

### 2.3 盲区电容的成因（255 个）

| 成因 | 数量 | 能否靠 KB 解决 |
|---|---:|---|
| 对端 IC 脚**类别解析不出来**（KB 缺型号或缺引脚名映射） | 127 | ✅ 能 |
| 两端都不落地 / 无 GND 端（不是去耦，是滤波/耦合/吸收） | 108 | ❌ 不能，要另做聚类 |
| 对端 IC 脚类别 = power_in/power_out | **20** | ✅ 已是可用证据 |

### 2.4 KB 引脚类别可用率（核心指标）

| 指标 | 数值 |
|---|---|
| 全仓主芯片型号总数 | 52 |
| KB 里**完全没有**该型号 | **21** |
| 型号在 KB、但解析不出任何引脚类别 | **24** |
| 可正常解析引脚类别 | **7**（JW5065、PCF8563 等） |
| KB 总条目 | 89（其中含 `RXD` / `JTAG/C2` / `PC-USB` / `CON_2P_1.2MM` 等被误导入的网名/连接器型号，见 §六·C） |

---

## 三、目标与非目标

### 目标

1. **把 KB 引脚类别可用率从 13% 提到 ≥ 70%**（52 种型号中 ≥ 36 种可解析）。
2. **KB 有型号但解析不出类别**的那 24 种必须查清原因：是 pinout 缺脚、还是引脚名不在 `chip_resolver` 的映射表里 —— 两类修法完全不同。
3. 补录后重跑全仓量化，盲区电容中"因类别解析失败"的那 127 个应显著下降。

### 非目标（明确不做）

- 🚫 不为了刷覆盖率硬造模板（**防误报优先于防漏报**）。
- 🚫 不改 `dc-decoupling-cap` 等既有模板的判定语义（会波及全部板，见 §四·B 的风险）。
- 🚫 不引入 LLM 调用 / 网络请求进识别链路（`cg role` 默认全离线是产品承诺）。
- 🚫 本 PRD 不动基准层（基准层由主会话经 `nl_to_jsonl.py` 回写）。

---

## 四、方案

### A. KB 引脚类别补录（主线，P0–P4）

**A1. 型号缺失的 21 种 —— 走 datasheet → JSON → 导入**

现有流程：`scripts/import_pin_json.py`（见 memory: circuit-graph 知识库引脚录入流程）。
datasheet 目录取自 `.env` 的 `DATASHEET_DIR`。

按「出现次数 × 覆盖板数」排序，先做这 5 档：

| 优先级 | 型号 | 出现次数 | 覆盖板 | 类型 |
|---|---|---:|---|---|
| P0 | LM358 | 9 | M4-V8.03 / add-primitives / 无码接收 | 运放（最通用） |
| P0 | MAX6675 | 3 | T193 / smoke-main-V05 | 热电偶测温 |
| P0 | HT7333 | 1 | T193 | LDO |
| P0 | LTC4054 | 1 | T193 | 锂电充电 |
| P0 | AT45DB321D-SU | 1 | T193 | SPI Flash |
| P0 | C8051F320_342/3/6/7 | 1 | T193 | 主控 MCU |
| P0 | HT1621B | 1 | T193 | LCD 段码驱动 |
| P1 | 24C64 / 78M05 / N76E003AT20 | 3 | M4-V8.03 | EEPROM / LDO / MCU |
| P1 | PN8048 / NSD1624-DSPR / 78L15 / SIYO6358 / MM32G0001-SSOP20 | 5 | 500W-TX-Main-Zero | 电源/驱动/MCU |
| P2 | HS2262 A-R4 | 1 | 遥控器2 | 遥控解码 |
| P2 | 24L15 / TP4056 / DW02KA / mcu_sop16 / PH2.54*4 / MRF521R | 6 | 其余板 | 杂项 |

**A2. 型号在但类别解析不出来的 24 种 —— 先查因，再修**

两种可能，修法不同：
- (i) **pinout 缺脚**：KB 里该型号的 pinout 条目不完整 → 补 pinout。
- (ii) **引脚名映射不到类别**：pinout 齐全，但引脚名（如 `VL`、`VOUT`、`AVDD`）不在 `circuit_graph/knowledge` 的引脚名→category 映射里 → **补映射表**（这是一次性的、可复用的改进，比补单个型号划算得多）。

> 这一档的诊断脚本必须先写出来（见 §五·P1 的产出物），否则无法区分 (i) 和 (ii)。

**A3. KB 数据卫生**

现有 89 条里混入了 `RXD`、`JTAG/C2`、`PC-USB`、`CON_2P_1.2MM`、`Coil`、`GREEN`、`RED`、`PE`、`JW`、`HT` 等**网名 / 位号 / 颜色 / 残缺字符串**。
这些不是元件型号，会污染检索与自动匹配。建议加一条 KB 导入校验：part_number 至少包含一个数字或长度 ≥ 4 且不是纯已知连接器名。

### B. 模板侧：IC 电源脚旁证（次线，P5，可选）

**B1. 技术前提（已验证）**

`circuit_graph/vf2_matcher.py` 的组合语义（L553–567）：

```
name_ok = signal_name_matches(name_pattern, net_name)
if expected_pin_category is not None and (not name_ok or not pin_category_fallback):
    → 强制检查 pin category
```

即 **`pin_category_fallback` 缺省 false 时，pin category 是 AND 条件**；若为 true 且网名匹配则该检查被跳过。
另外 `_pin_category_fallback_allowed()` 要求网名以 `Net` 开头（自动命名网）才允许"靠类别救人工命名的网"。

结论：**要表达"IC 电源脚 ↔ GND 的电容"，正确写法是新增一个 `expected_pin_category: "power_in"` 且
`pin_category_fallback` 缺省（false）的信号**，`name_pattern` 用 `.*`，不设 `expected_role`。

**B2. 为什么不能直接改 `dc-decoupling-cap`**

`dc-decoupling-cap` 现有信号是 `name_pattern: ".*"` + `expected_role: "power"`。
给它加 `expected_pin_category` 只会**收紧**（AND），不会放宽 —— 因为 `.*` 恒匹配、`pin_category_fallback` 为 false 时
pin 检查强制执行、且 `expected_role: power` 仍要满足。而问题恰恰是自动命名网拿不到 power role。

**B3. 方案对比**

| 方案 | 做法 | 收益 | 风险 | 结论 |
|---|---|---|---|---|
| B3-a | 改 `dc-decoupling-cap`（加 pin_category） | 0（反而收紧） | 回退已有命中 | ❌ |
| B3-b | 新增专用模板 `ic-power-pin-bypass-cap`（power_in 脚 ↔ GND，不设 expected_role） | 全仓 **20** 个电容 | 与 `dc-decoupling-cap` 重复命中 → 需去重规则 | 🟡 可做 |
| B3-c | 改通用 role 规则（`role_rules.py` 的 decoupling 规则） | 与 B3-b 同量级 | 不进模板块，覆盖报告无变化 | 🟡 备选 |
| B3-d | 扩展模板语言，支持"role=power **或** 有 power_in 脚" | 20 + 未来同类 | 模板语言改动，影响所有模板的语义 | ❌ 本轮不做 |

**B4. 现实约束**：B3-b 的 20 个里，**T255-3.0 只有 2 个**（107 个盲区电容里），
因为该板 14 颗主芯片里只有极少数能解析出电源脚类别 —— 这正是 §四·A 的价值所在：**KB 补上后，这个数字会自己涨。**

### C. 基准层扩面（P4，与 A 并行做）

现在只有 2/15 块板有基准层。补齐后，每次 KB/模板改动都能立刻看到全局影响，
否则"改好了"只是在这一块板上成立。

优先级：T255-3.0（342 元件，最大）→ smoke-main-V05（119）→ 500W-TX-Main-Zero（123）→ M4-V8.03（84）。

做法：`cg role --all` → 审阅 → `nl_to_jsonl.py --source cg-role` 落库（source=session-correction/human 的行需工程师确认）。

---

## 五、实施步骤

| 步 | 内容 | 产出物 | 验收 |
|---|---|---|---|
| **P0** | 写 KB 诊断脚本：逐型号报告「KB 有无 / pinout 脚数 / 可解析类别数 / 缺口原因」 | `scripts/kb_pin_audit.py` | 52 种型号全覆盖报告 |
| **P1** | 按报告把 45 种缺口拆成「(i) 缺 pinout / (ii) 缺引脚名映射」两类并计数 | 分类清单 | 每种型号归一类 |
| **P2** | 补 (ii) 类的**引脚名→category 映射表**（一次性改进，收益最大） | 映射表扩充 | 24 种「有型号无类别」里能救回多少 |
| **P3** | 按 §四·A1 优先级补录 (i) 类型号的 pinout（datasheet → JSON → `import_pin_json.py`） | KB 增量 | 每种型号 `cg kb pinout` 有输出 |
| **P4** | 给 T255-3.0 / smoke-main-V05 / 500W-TX-Main-Zero / M4-V8.03 建基准层 | 4 个 `.answers.jsonl` | 各板 `coverage_test` 有报告 |
| **P5** | 视 P2/P3 结果决定是否做 §四·B3-b 模板 | `ic-power-pin-bypass-cap`（或放弃） | 全仓量化：新增命中 vs 误报 |
| **P6** | KB 数据卫生：清理网名/连接器型号误导入条目 | KB 清理脚本 + 校验 | `cg kb stats` 条目数下降且无有效条目丢失 |

**每步结束都要跑**：`.venv/Scripts/python.exe -m pytest -q` → **0 failed**（当前基线 958 passed / 4 skipped / 0 failed）。

---

## 六、验收标准

### 全局

1. `.venv/Scripts/python.exe -m pytest -q` → **0 failed / 958+ passed / 4 skipped**（失败数不得增加）。
2. 既有模板的 fixture CASE **一条都不能红**，尤其 `dc-decoupling-cap` / `pull-up-resistor` /
   `voltage-divider*` / `buck-converter` / `jw5065-buck`（KB 补录会改变 `expected_pin_category` 的判定结果）。
3. `cg role --all` 在 T193 / FX-T268 上**不得回退**（当前：T193 有效对齐 100%、FX-T268 有效对齐 100%）。

### KB 专项

4. KB 引脚类别可用率 ≥ **70%**（当前 13%）。
5. 每种新补录型号：正例（该型号的某电源脚被认成 `power_in`）+ 反例（信号脚**不得**被认成电源脚）。
6. KB 导入后 `cg kb stats` 的 components/pinouts 增量与导入条目数一致。

### 识别效果

7. 重跑全仓量化（§二·2.3 的同一脚本）："因类别解析失败"的盲区电容应从 **127** 显著下降。
8. 新模板（B3-b，若做）必须给出**正例 + 反例**测试，且反例要覆盖：
   - 栅极 RC 吸收电容（500W-RX C4/C5：1µF 挂 U2.2 的 K 脚）——**不得**被认成旁路电容
   - 电源轨之间 / 非 GND 端的电容 —— **不得**命中
   - 运放反馈电容 —— **不得**命中

### 过程纪律

9. 🚫 任何一步都**不得删改既有测试断言**来让失败消失；确需变更断言时，在提交说明里写清「改了哪条、为什么」。
10. 🚫 `git add -A` 前先看 `git status`（仓库有意保留未提交的 WIP）。
11. 🚫 不 amend 已有 commit；改错用新提交还原。

---

## 七、附带发现（不在本轮主范围，建议记入 backlog）

| # | 发现 | 位置 | 影响 |
|---|---|---|---|
| A | Protel 解析器 `device_kind` 按位号前缀猜类型，**`Coil` 前缀不在表里** → 电感被认成电容（500W-RX `Coil1` 值 10µH 却报 Capacitor） | `circuit_graph/parsers/protel.py::_infer_device_kind` | 中 |
| B | 108 个盲区电容"两端都不落地/无 GND 端"，本质是滤波/耦合/吸收电容，与去耦是两类东西，需单独聚类再定模板 | — | 中 |
| C | KB 混入网名/连接器型号（`RXD`/`JTAG/C2`/`PC-USB`/`CON_2P_1.2MM`/`Coil`/`GREEN`/`RED`/`PE`/`JW`/`HT`） | KB 数据 | 低（但污染检索） |
| D | `遥控器2` 5 个盲区件（C1/L1/L2/Q1/TX）是射频件，无对应电路模板属正常 | — | 无（不用管） |
| E | `cg role` 对 Q1/Q2 不报模板块（走泛化 `transistor` 规则），导致基准 block 只能记 `(unmatched)` | `circuit_graph/role.py` | 低 |

---

## 八、开放问题（待拍板）

1. **KB 补录的深度**：只补到"引脚名 → 类别"够不够？还是要连 pinout 全表（每个脚的功能描述）都录？
   后者数据量大得多，但能让 `cg role` 的 IC 定性从"按接法猜"升级为"按型号查"。
2. **是否接受模板侧只多捞 20 个电容**（B3-b）？如果目标是"大幅提升可识别率"，
   我的判断是**先把 KB 做到 70%，模板侧的数字会自己从 20 涨到几百**。
3. **基准层扩面要铺到几块板**？全铺（4 块大板）还是先铺 T255-3.0 一块？
4. **§七·A（Coil 前缀）要不要顺手修**？改动小，但属于解析器层，需要单独走一次回归。

## 8.1、开放问题（拍板）
1.**KB 补录的深度** 连 pinout 全表
2.**是否接受模板侧只多捞 20 个电容** 同意你的判断**先把 KB 做到 70%，模板侧的数字会自己从 20 涨到几百**。
3.**基准层扩面要铺到几块板**T255-3.0 一块（铺设方法：1.提取全部元件名，2.ccc子代理kb全部录入[录不全的cli,上网查]，3.cg role,4.ccc子代理结合cg role,并从网表调研元件用途生成临时的{.answers.jsonl}给我审查,5.我将校准过的{.answers.jsonl}回传，大致这个流程，你有更好的流程？）
4.**（Coil 前缀）要不要顺手修** 要
