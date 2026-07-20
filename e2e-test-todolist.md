# circuit-graph E2E 测试清单

> 从人类使用视角验证 `refactor(knowledge): rename pin2json → chip_resolver + pin_source + batch` 的变更。
> 测试文件: `fixtures/protel/FX-T268-V6-10米-5065.NET`（默认）
>
> **2026-07-20 更新**：`todolist_fix.md` 已重写为查询引擎修复计划（P0 neighbors_of 性能 / P1 遍历属性谓词 / P2 边语义 / P3 约束路径）；§十 回归表按新 P0–P3 重写；§二 2.4 的 fallback 参考改指 Readme.md。
>
> **2026-07-13 审计更新（历史）**：旧版 `todolist_fix.md` 的 P0/P1（sexpdata 依赖、cg sim warning、死代码、ERC 格式提示、SKILL 修正）已完成并回归，对应旧 §十 行已随新版重写移除。

---

## 零、环境准备

```powershell
cd E:\__electric\__workspace\stacking\circuit-graph

# 0.1 确认依赖完整（pyproject.toml 已包含 sexpdata；如未含则 `pip install sexpdata` 临时补救）
uv run cg --version
# 若 ModuleNotFoundError：先 `uv pip install sexpdata>=1.0.0` 再继续

# 0.2 清空旧 DB，重新初始化
Remove-Item circuit_graph\knowledge\circuit_graph.db -Force -ErrorAction SilentlyContinue
uv run cg kb init

# 0.3 导入默认网表（会触发 JLCPCB 网络调用 + chip_resolver 离线解析；离线可改用 --dry-run 预览）
uv run cg kb import protel "fixtures/protel/FX-T268-V6-10米-5065.NET"
```

---

## 一、pin2json CLI 仍正常工作

| # | 场景 | 命令 | 预期 |
|---|------|------|------|
| 1.1 | 搜索 + 解析型号 | `pin2json LM321 -p SOT-23-5` | 输出 JSON，含 `symbol`, `package`, `source`, `pin_count`, `pins`, `pin_types` |
| 1.2 | 直传 C-id | `pin2json C9405` | 同上，不经过 JLCPCB 搜索 |
| 1.3 | 解析本地 .kicad_sym | `pin2json <path>/some.kicad_sym` | 直接解析文件中的引脚 |
| 1.4 | 搜索失败优雅降级 | `pin2json XYZZY_DOES_NOT_EXIST` | 打印错误到 stderr，exit 1 |
| 1.5 | `--help` | `pin2json --help` | 打印用法说明 |

### 1.1 详细检查

- [ ] `pins` 字段是 `{"1": "+IN", "2": "GND", ...}` 格式
- [ ] `pin_types` 字段存在，每个引脚都有分类（如 `"1": "input"`, `"2": "ground"`）
- [ ] `pin_types` 的键和 `pins` 的键完全相同
- [ ] 无 Markdown 包裹，纯 JSON 输出

---

## 二、cg view 引脚名补全

| # | 场景 | 命令 | 预期 |
|---|------|------|------|
| 2.1 | IC 无 KB 数据时 pin.name=pin 号 | `uv run cg view U1 fixtures/protel/FX-T268-V6-10米-5065.NET --pretty --hops 0` | HT7533 引脚名默认为引脚号 (1/2/3)，net 正确 |
| 2.2 | 电阻不参与引脚名补全 | `uv run cg view R100 fixtures/protel/FX-T268-V6-10米-5065.NET --pretty --hops 2` | 正常显示 degree/kind/net，不尝试补全 passive 引脚名 |
| 2.3 | 未知元件 | `uv run cg view Z99 fixtures/protel/FX-T268-V6-10米-5065.NET --pretty` | `"error": "component not found: Z99"` |
| 2.4 | `--with-power` | `uv run cg view U2 fixtures/protel/FX-T268-V6-10米-5065.NET --with-power` | 输出含 `power_context` 字段；5V2 rail 已知会 fallback 到 `source_kind: "unknown"`（见 Readme.md「`--with-power` fallback 行为」） |
| 2.5 | `--with-comm` | `uv run cg view U2 fixtures/protel/FX-T268-V6-10米-5065.NET --with-comm` | 输出含 `comm_context` 字段，检测到 I2C/UART/PWM |
| 2.6 | `--with-all` | `uv run cg view U2 fixtures/protel/FX-T268-V6-10米-5065.NET --with-all` | 同时包含 power + comm context |
| 2.7 | 被动元件不补 pin name | `uv run cg view R101 fixtures/protel/FX-T268-V6-10米-5065.NET --pretty --hops 0` | R101 的 pins[] 不含 `name` 字段；仅 IC 引脚从 KB 补 name |

---

## 三、知识库查询（pin_source 显示）

| # | 场景 | 命令 | 预期 |
|---|------|------|------|
| 3.1 | lookup 导入元件 | `uv run cg kb lookup JW5065` | 输出含 `pin_source`, `is_credible`, `pinout` |
| 3.2 | lookup 不存在的元件 | `uv run cg kb lookup XYZ_NOT_EXIST` | `{"error": "not found: XYZ_NOT_EXIST"}` |
| 3.3 | 全文搜索 | `uv run cg kb search Holtek` | 返回匹配结果 |
| 3.4 | 只看引脚 | `uv run cg kb pinout <part_number>` | `{"1": "name1", ...}` |
| 3.5 | 统计 | `uv run cg kb stats` | 输出 components/pinouts/properties/provenance/categories 计数 |

### 3.1 详细检查

- [ ] `pin_source` 字段存在
- [ ] `is_credible` 字段仍存在（向后兼容）
- [ ] `pinout` 字段为 `{"pin_number": "pin_name", ...}` 格式

---

## 四、cg kb import（--symbol-dir 新选项）

| # | 场景 | 命令 | 预期 |
|---|------|------|------|
| 4.1 | import protel 默认 | `uv run cg kb import protel fixtures/protel/FX-T268-V6-10米-5065.NET` | `Imported N component(s)` |
| 4.2 | import protel + --symbol-dir | `uv run cg kb import protel fixtures/protel/FX-T268-V6-10米-5065.NET --symbol-dir ./some/sym/dir` | 同上 + 优先从本地符号库解析 |
| 4.3 | import kicad | `uv run cg kb import kicad fixtures/kicad/FX-T268-V6-10米-5065-kicad.net` | 解析 KiCad 网表格式 |
| 4.4 | import kicad + --symbol-dir | `uv run cg kb import kicad fixtures/kicad/FX-T268-V6-10米-5065-kicad.net --symbol-dir ./some/sym/dir` | 同上 + symbol-dir |
| 4.5 | import json | `uv run cg kb import json circuit_graph/knowledge/seed/fx_t268_seed.json` | 解析 seed JSON |
| 4.6 | import 目录 | `uv run cg kb import protel fixtures/protel/` | 递归导入目录下所有 .NET 文件 |
| 4.7 | 重复导入幂等 | 连续两次 `uv run cg kb import json circuit_graph/knowledge/seed/fx_t268_seed.json` | 第二次不开新行，数据不变 |

### 4.2 详细检查

- [ ] 若 `--symbol-dir` 目录下有匹配的 `.kicad_sym` 文件，优先使用（pin_source=kicad_official）
- [ ] 若本地无匹配，回退到 JLCPCB 搜索
- [ ] 不指定 `--symbol-dir` 时行为与之前完全一致

---

## 五、cg kb resolve（三阶段回退）

| # | 场景 | 命令 | 预期 |
|---|------|------|------|
| 5.1 | resolve 默认 | `uv run cg kb resolve fixtures/protel/FX-T268-V6-10米-5065.NET` | JSON 输出 `resolved_pins` + `warnings` |
| 5.2 | resolve + --symbol-dir | `uv run cg kb resolve fixtures/protel/FX-T268-V6-10米-5065.NET --symbol-dir ./sym1 --symbol-dir ./sym2` | 支持多个 --symbol-dir |
| 5.3 | resolve --dry-run | `uv run cg kb resolve fixtures/protel/FX-T268-V6-10米-5065.NET --dry-run` | 仅报告，不实际调用解析器 |
| 5.4 | 解析后 lookup 验证 | resolve 后 `uv run cg kb lookup <某个IC型号>` | 引脚名已补全，pin_source 已设置 |

### 5.4 详细检查

- [ ] 若有 JLCPCB 网络，解析后元件 `pin_source` 应为 `"jlcpcb"`
- [ ] 若有本地 `.kicad_sym` 匹配，`pin_source` 应为 `"kicad_official"`

---

## 六、导出功能回归

| # | 场景 | 命令 | 预期 |
|---|------|------|------|
| 6.1 | 导出 graph JSON | `uv run cg export graph fixtures/protel/FX-T268-V6-10米-5065.NET` | `output/circuit.graph.json` 生成 |
| 6.2 | 导出 GraphML | `uv run cg export graphml fixtures/protel/FX-T268-V6-10米-5065.NET` | `output/circuit.graphml` 生成 |
| 6.3 | cg --version | `uv run cg --version` | 输出版本号 |

---

## 七、边缘情况

| # | 场景 | 命令 | 预期 |
|---|------|------|------|
| 7.1 | 无网络时 import | 断网执行 `cg kb import protel ...` | pin2json 解析静默跳过，不报错 |
| 7.2 | jlc CLI 未安装 | 未安装 `@jlcpcb/cli` 时 import | 静默跳过 JLCPCB 步骤 |
| 7.3 | 空网表文件 | 导入空文件 | 返回 0，无异常 |
| 7.4 | 损坏的网表 | 导入乱码文件 | 适度容错，不崩溃 |
| 7.5 | 中文路径 | 网表放在含中文的目录中 | 正常解析 |
| 7.6 | 多网表目录 | 目录下有多个 .NET + 非网表文件 | 只导入 .NET，跳过其他文件 |
| 7.7 | DB 不存在时 cg view | 删除 .db 后执行 `cg view R100 ...` | 不崩溃，KB 缺失时优雅降级 |

---

## 八、引脚语义分类验证

| # | 检查项 | 验证方式 |
|---|--------|---------|
| 8.1 | VCC/VDD 分类为 power_in | `pin2json LM321 -p SOT-23-5` 后检查 `pin_types["5"]` = `"power_in"`（VCC） |
| 8.2 | GND 分类为 ground | `pin2json LM321 -p SOT-23-5` 后检查 `pin_types["2"]` = `"ground"` |
| 8.3 | IN+/IN- 分类为 input | `pin2json LM321 -p SOT-23-5` 后检查 `pin_types["1"]` = `"input"`（+IN） |
| 8.4 | OUT 分类为 output | `pin2json LM321 -p SOT-23-5` 后检查 `pin_types["4"]` = `"output"` |
| 8.5 | 未知引脚名 fallback | 非标准引脚名 `pin_types` 值为 `"unknown"` |
| 8.6 | PA0-PA15 分类为 gpio | 解析任意 STM32/mm32 MCU 的 pin2json 结果 |
| 8.7 | SDA/SCL 分类为 comm | 解析任意 I2C 器件的 pin2json 结果 |
| 8.8 | pin_types 存储到 DB | `cg kb import ...` 后查 `pinouts` 表确认 `pin_type` 列已填充 |

---

## 九、原理图自动布局 E2E 验证

> 验证 `circuit_graph.layout` 对 LM321 CCS 的自动布局结果符合硬编码规则：左侧控制电路、右侧功率通路、Reference/Value 0°、正交布线、无重叠、无引线与 wire 重合。

### 9.1 生成分布图

| # | 场景 | 命令 | 预期 |
|---|------|------|------|
| 9.1 | 自动布局生成 | `uv run python lm321_ccs/convert_to_kicad.py output/test_layout.kicad_sch` | `output/test_layout.kicad_sch` 生成，exit 0 |
| 9.2 | ERC 无 error | `uv run cg erc check --engine kicad output/test_layout.kicad_sch` | `error_count: 0`（仅允许预先存在的 `lib_symbol_mismatch` warning）；**注意 `--engine kicad` 需 `kicad-cli` 在 PATH，否则回退报错而非自动切到 native** |
| 9.3 | ERC native 引擎 | `uv run cg erc check output/test_layout.kicad_sch` | 同 9.2 但走 native 4-validator；不依赖 kicad-cli |

### 9.2 布局规则检查

| # | 检查项 | 验证方式 |
|---|--------|---------|
| 9.2.1 | LM321 在左 | 解析 `.kicad_sch`，`U1` 的 `(at x y)` 中 x < 70 mm |
| 9.2.2 | BC547 在右 | 解析 `.kicad_sch`，`Q1` 的 `(at x y)` 中 x > 90 mm |
| 9.2.3 | 功率柱竖直排列 | `RL`、`Q1`、`RS` 的 x 坐标偏差 < 5 mm，y 坐标自上而下递增 |
| 9.2.4 | 控制组竖直排列 | `RREF1`、`RREF2`、`U1`、`RB` 的 x 坐标偏差 < 10 mm，y 坐标自上而下递增 |
| 9.2.5 | Reference 0° | 所有 `(property "Reference" ...)` 的 `(at x y 0)` 角度为 0 |
| 9.2.6 | Value 0° | 所有 `(property "Value" ...)` 的 `(at x y 0)` 角度为 0 |
| 9.2.7 | 网格对齐 | 所有 symbol `(at x y r)`、wire 端点、junction、label 的 x/y 为 2.54 mm 整数倍 |
| 9.2.8 | 正交走线 | 所有 wire 段为水平或垂直（`x1==x2` 或 `y1==y2`） |
| 9.2.9 | 无引线与 wire 重合 | 对每一个 pin，其引线方向线段不得与任何 wire 段共线重叠 |
| 9.2.10 | 标签不重叠 | 计算所有 Reference/Value 文本包围盒，两两无交集 |
| 9.2.11 | Y 方向间距 | 竖直相邻元件/标签/电源符号的 Y 间距 ≥ 1.27 mm |
| 9.2.12 | 电源符号完整 | 生成图中包含 `+12V`、`GND`、`PWR_FLAG` 且 ERC 电源驱动通过 |

### 9.3 与手工布局对比

| # | 检查项 | 验证方式 |
|---|--------|---------|
| 9.3.1 | 结构等价 | `output/test_layout.kicad_sch` 与 `lm321_ccs/1A_CCS_LM321_BJT.kicad_sch` 网表一致（可用 `cg` 或 KiCad netlist export 比较） |
| 9.3.2 | 视觉一致 | 关键元件（U1/Q1/RL/RS/RREF1/RREF2/RB/CF）位置偏差在 5 mm 以内 |

---

## 测试数据准备

```powershell
# 清理
Remove-Item circuit_graph\knowledge\circuit_graph.db -Force -ErrorAction SilentlyContinue

# 初始化（含 vendor_db.json 迁移）
uv run cg kb init

# 导入默认网表
uv run cg kb import protel "fixtures/protel/FX-T268-V6-10米-5065.NET"

# 验证
uv run cg kb stats
uv run cg kb lookup JW5065
uv run cg view R100 fixtures/protel/FX-T268-V6-10米-5065.NET --pretty --hops 0
```

---

## 十、回归：todolist_fix.md（查询引擎修复）完成项 sanity check

> 每完成一项修复跑对应行；2026-07-20 按新版 todolist_fix.md 重写

| # | 触发修复 | 命令 | 预期 |
|---|---------|------|------|
| 10.1 | P0（neighbors_of O(E) 修复） | `uv run pytest -k neighbor` + `uv run cg view R100 "fixtures/protel/FX-T268-V6-10米-5065.NET" --pretty --hops 2` | 测试全绿；view 输出与修复前逐字段一致（纯性能修复，无行为变化） |
| 10.2 | P1-任务2（net_types/device_kinds 谓词） | `uv run pytest -k traverse` | 新增用例通过：fixture 上 BFS `net_types=[NetType.POWER]` 结果只含 power/gnd 节点 |
| 10.3 | P1-任务3（power_tree_recognizer 重写） | `uv run cg view U2 "fixtures/protel/FX-T268-V6-10米-5065.NET" --with-power` | 输出 schema 不变；5V2 rail 仍 fallback 到 `source_kind: "unknown"`（同 §二 2.4） |
| 10.4 | P2-任务4（边语义 enricher） | `uv run cg export graph fixtures/protel/FX-T268-V6-10米-5065.NET` 后检查 `output/circuit.graph.json` | pin–net 边携带 `metadata.direction` / `metadata.role`；IC 输出脚 direction=source、被动元件 bidirectional |
| 10.5 | P2-任务5（边语义谓词遍历） | `uv run pytest -k role` | `neighbors(U2.OUT, depth=2, role=analog)` 类查询只沿 analog 边扩展，power/gnd 不混入 |
| 10.6 | P3-任务6（约束最短路径） | `uv run pytest -k path` | USB→U2.VDD 的 power-only 路径存在；DC-only 路径不穿过串联电容 |
