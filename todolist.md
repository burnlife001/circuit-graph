# TODO — circuit-graph 遗留问题清单

> 来源：2026-10-22 T268-V6（`fixtures/protel/FX-T268-V6-10米-5065.NET`，207 元件）电路分析实战。
> 每条含验收标准；完成后从清单移除并恢复对应的 CI 门禁/skip。
> 进度（2026-10-22）：新建本清单，8 条待办（P0×3 / P1×3 / P2×2）。
> 2026-10-22 复核：全部硬数据已复测（见下方「基线」），修正 3 处事实偏差 + 拆分 1 条。

## 基线（2026-10-22 实测，作为验收参照）

| 指标 | 实测值 |
|------|--------|
| `cg view C9` 端到端 | **12.8 s** |
| `cg view C9` 输出体积 | 4117 B（components 3919 / nets 2717 / `nets.GND.members` 1058） |
| `cg view Q5 --with-blocks` 输出体积 | 28774 B（components 24419 / nets 9886 / blocks 5550） |
| `cg view Q5 --with-blocks` 中 87 个元件 | **85 个是 GND 挂载元件**（占 components 段 13077 B = 54%），这是体积主因 |
| 全板功能块 | 59 个（pull-down 26 / pull-up 15 / voltage-divider 4 / pmos 4 / differential-comparator 2 / peak-envelope 2 / nmos 2 / type-c-cc-pull-down 2 / inverting-amplifier 1 / current-shunt 1） |
| 模板匹配数 | nmos-low-side-switch **232**、pmos-high-side-switch **256**、pull-down 28、pull-up 15、differential-comparator 10、voltage-divider 4 |
| 2 s 超时被 abort 的模板 | **4 个**：sallen-key-low-pass / lc-pi-filter / rc-high-pass / rc-low-pass |
| 超时放宽后的真实代价 | rc-low-pass 4.85 s / 106 匹配；rc-high-pass 4.82 s / 14 匹配；lc-pi-filter 2.02 s / 0 匹配（90 s 上限内自然结束）；**sallen-key-low-pass 90 s 仍超时** |

---

## 执行顺序（依赖关系）

```
P0-2 (透出 warnings)  →  P0-1 (预筛+提速)  →  P0-3 (缓存)
                          ↘  P1-5 (假阳性治理)
```

先做 P0-2：它工作量最小（约 20 行），且是 P0-1/P1-5 的"可信度底线"——不做它，无法判断提速/降噪是否真的没漏匹配。

---

## P0 — 拓扑识别的性能与正确性

### 1. 透出 enricher warnings（结果可信度底线）
- 现状：**两层丢弃**，不只是 CLI 层。
  1. `circuit_graph/enrichers/__init__.py:44-79` 的 `run_enrichers` **从不读取 `enricher.warnings`**——它只收集管线级警告（`ENRICH_DEPENDENCY_MISSING` / `ENRICH_CANENRICH_ERROR` / `ENRICH_EXECUTION_ERROR` / `canEnrich=false`）。`TopologyRecognizer.warnings` 里装的 `TOPOLOGY_MATCH_ABORTED`（`topology_recognizer.py:53`）在这一层就被丢掉。
  2. `circuit_graph/cli/app.py:474` 的 `_parse_and_enrich` 又只取 `.ir`，丢弃 `EnricherResult.warnings`。
  实测：`run_enrichers(...)` 返回的 warnings 只有 `['MultiUnitSplitter: skipped (canEnrich=false)']`，**一条 `TOPOLOGY_MATCH_ABORTED` 都没有**。
- 建议：
  1. `run_enrichers` 在 `e.enrich(ir)` 之后追加 `getattr(e, "warnings", [])`；
  2. `_parse_and_enrich` 改为返回 `(ir, warnings)`；
  3. `cg view` / `cg export graph` 在 JSON 顶层加 `"warnings": [...]`，同时 stderr 打印一行摘要。
- **验收标准**：
  1. 默认网表下 `cg view C9 <netlist>` 的 JSON 顶层出现 `"warnings": []`（键存在，不是缺键）；
  2. 人为把 `timeout_seconds` 调到 0.001 s，JSON 里能看到 `TOPOLOGY_MATCH_ABORTED`；
  3. `run_enrichers` 返回的 warnings 里直接含 enricher 级警告（单元测试断言）。

### 2. 拓扑匹配候选集预筛 + 可配置超时
- 现状：`cg view` / `cg export graph` 端到端 **12.8 s**，~95% 落在 `TopologyRecognizer`。`enrichers/topology_recognizer.py:50` 对每个模板硬编码 `timeout_seconds=2.0`，实测 4 个模板在 2 s 内 abort（matches=0）：`sallen-key-low-pass` / `rc-low-pass` / `rc-high-pass` / `lc-pi-filter`；放宽到 90 s 后 `rc-low-pass` 用 4.85 s 找到 **106 个匹配**、`rc-high-pass` 4.82 s 找到 14 个——当前整类 RC 滤波功能块被**静默丢弃**。
- **关键约束**：单纯调大超时解决不了问题，反而更慢。
  - `sallen-key-low-pass` 在 90 s 上限下**依然超时**（0 匹配），说明它是模板/候选集本身的问题；
  - `lc-pi-filter` 在 90 s 上限下 2.02 s 自然结束、0 匹配——它只是被 2.0 s 的刀刃切掉了；
  - 若默认超时直接提到 10 s，端到端会**变慢到 ~21 s**。所以「预筛」是前提，「超时可配」是兜底。
- 建议：
  1. 用模板的 `signals[].expected_role` / `name_pattern` 先把候选 net 缩到几十个，再在候选集上跑 VF2，而不是全图两两搜；
  2. 超时改为可配置（CLI `--topology-timeout`，默认 10 s），预筛后实际用不满；
  3. 加匹配数上限 `max_matches`，先按 role 分组再评分；
  4. **顺带修**：现循环内每个模板都重建一次 `Vf2TopologyMatcher`（`topology_recognizer.py:49`），25 次重复建图；预筛后应提到循环外。
- **验收标准**：
  1. **冷启动**（缓存未命中）`cg view C9 <netlist>` 端到端 < 1.5 s；
  2. 默认配置下 `rc-low-pass` / `rc-high-pass` / `lc-pi-filter` / `sallen-key-low-pass` **均不再出现 aborted**；
  3. `rc-low-pass` 能命中（验收表 #5）；
  4. 被跳过的模板能在命令输出里被看见（配合第 1 条的 warnings 通道）。

### 3. IR 解析缓存
- 现状：每次 `cg view` 都重新 parse + 跑完整 enricher 管线（12.8 s），同一网表的重复查询全部重算。
- 建议：缓存键 = 网表内容 sha256 + enricher 版本号 + primitives 库 hash；缓存 IR JSON 到 `<repo>/.cg-cache/`，`_parse_and_enrich` 命中即返回。
- **验收标准**：
  1. 同一网表**第二次** `cg view`（缓存命中）< 0.3 s；
  2. 网表内容改动后缓存自动失效（不返回陈旧 IR）；
  3. 缓存目录可通过 CLI 参数指定，默认不写进版本库。

---

## P1 — 查询层与语义精度

### 4. `cg query` 紧凑查询命令
- 现状：只有 `cg view`（整块 ego-subgraph JSON，4.1 KB，其中 85 个 GND 挂载元件占 components 段 54%）和 `cg export graph`（整份 JSON），**没有"回答单个问题"的入口**；实际使用被迫先 export 再写脚本查 JSON。
- 建议：新增 `circuit_graph/cli/query.py`：`cg query <file> --conn X --comp A B --net N --find STR --all --type T --format text|json`。text 输出为紧凑表格（位号 / 类型 / 参数值 / 型号 / 引脚→网络，IC 带引脚名）。
- **验收标准**：
  1. `cg query <netlist> --conn C9` 输出 C9 每个引脚所在网络及同网元件（含参数值），行数 < 20，且包含 `NetC9_2`；
  2. `--format json` 输出结构可被 `jq` 直接处理；
  3. 支持 `--find 4.7nf` 这类按参数值/型号的模糊检索。

### 5. 匹配假阳性治理
- 现状：Q5（26 脚 IC，型号 SW5001）同时被识别为 `nmos-low-side-switch-q5` 和 `pmos-high-side-switch-q5`（已复现）；`nmos-low-side-switch` / `pmos-high-side-switch` / `rc-low-pass` 分别产生 232 / 256 / 106 个匹配；`peak-envelope-detector` 把 VBAK_A 的分压采样电路认成"峰值包络检波"（C56/C58两块）。匹配爆炸既是下游耗时来源，也是功能块列表的噪音源。
- 建议：给基元补 `value_pattern`（区分去耦电容与滤波容）；switch 类基元对多脚器件加器件类型 / 引脚数约束（SW5001 是双通道 buck，含 SW1/SW2/BST1/BST2/PWM1/PWM2 引脚，不该被当成单管 NMOS/PMOS）；命中数超阈值时降级为"候选"而非功能块。
- **验收标准**：
  1. `cg view Q5 --with-blocks` 不再同时出现 `nmos-low-side-switch-q5` 与 `pmos-high-side-switch-q5`；
  2. `nmos-low-side-switch` 匹配数从 232 降到 < 20；`pmos-high-side-switch` 从 256 降到 < 20；
  3. 全板功能块总数从 59 降到 < 45，且不残留低置信度噪声块；
  4. `rc-low-pass` 的 106 个匹配里，去耦电容类（1nF/100nF 且直挂地）被参数约束排除。

### 6. 新增基元：RC 吸收 / 自举电容
- 现状：现有 25 个基元里**没有** RC 吸收（snubber）、自举电容（bootstrap）；`power/peak-envelope-detector.json` 的描述中已出现"开关节点"字样，说明这类需求早已存在。板级实测（Q5/Q7 = SW5001 双通道 buck，4 个开关节点 SW1/SW2/SW3/SW4）每个开关节点都是同一套三元组：
  - **1nF 直通地**：SW1→C10、SW2→C12、SW3→C34、SW4→C35
  - **100nF 自举**（SW↔BST）：SW1↔C27↔NetC27_1(Q5-7/BST1)、SW2↔C28↔NetC28_1(Q5-13/BST2)、SW3↔C51↔NetC51_1(Q7-7)、SW4↔C52↔NetC52_1(Q7-13)
  - **2Ω + 4.7nF 吸收对**：SW1↔R25↔NetC7_2↔C7、**SW2↔R26↔NetC9_2↔C9**、SW3↔R39↔NetC31_2↔C31、SW4↔R40↔NetC33_2↔C33
- 建议：
  1. `primitives/protection/rc-snubber.json`：**三网路径** `SWx — R(2Ω) — NetCx_2 — C(4.7nF) — 地`（注意 C 不在 SWx 上，模板必须含中间网）；`value_pattern` 锁 R≈2Ω、C≈4.7nF；
  2. `primitives/power/bootstrap-cap.json`：C 接在 BST 与 SW 之间（`value_pattern` 锁 100nF）；
  3. `role=gnd` 必须同时匹配 `GND` 和 `CGND`——SW1/SW2 的吸收对落地于 GND，SW3/SW4 落地于 CGND。
- **验收标准**：
  1. `cg view C9 --with-blocks` 明确报告 C9 是 SW2 开关节点上的 RC 吸收电容（经 R26）；
  2. `cg view C27 --with-blocks` 报告 C27 是 SW1↔BST1 的自举电容；
  3. 4 个开关节点的吸收对、自举电容**各识别出 4 个实例**（不多不少）；
  4. 新基元通过 `scripts/build_primitive_index.py` 索引生成与 schema 校验。

### 7. 重复结构分组（对称性检测）— 从第 6 条拆出
- 现状：`primitives/schema.json` 无"重复结构分组"概念。**注意：整网对比并非 4 路完全对称**——SW1/SW3 成员为 {1nF, 100nF, Q, 2Ω, TP测试点}，SW2/SW4 成员为 {1nF, 100nF×1, 4×10nF NPO + 404 CBB, Q, 2Ω}，整网对比会得到 **2 组 × 2 路**。真正的重复单元是第 6 条那个**三元组**，它在 4 个开关节点上各出现一次。
- 建议：`primitives/schema.json` 扩展"重复结构分组"，分组键取**功能子结构**（如上述三元组）而非整个网；输出里列出 4 路对称组及各自位号。
- **验收标准**：
  1. 4 路开关节点的三元组被识别为**一个 4 路对称组**并在输出中列出（SW1/SW2/SW3/SW4）；
  2. 分组键为子结构而非整网——整网不等价的两组（SW1/SW3 与 SW2/SW4）仍归入同一组；
  3. 输出标注每路的差异件（测试点 T5/T12、额外 NPO 电容）。

---

## P2 — 体验与知识库

### 8. `cg view` 输出截断
- 现状：`cg view Q5 --with-blocks` 输出 28774 B，其中 87 个元件有 **85 个是 GND 挂载元件**（13077 B = components 段 54%）——高 degree 网络把所有挂载元件都展开成了完整 component 条目，这才是体积主因；`nets.GND.members` 本身只占 1058 B。功能块同理：与 focal 无关的块（pull-down 9 个等）也会出现，因为它们的元件被 GND 拖进 ego-subgraph。
- 建议：
  1. 高 degree 网络（默认 > 20）只展开与 focal 相邻的成员，其余折叠为 `(+N more)`，对应元件条目也一并折叠；
  2. 功能块只保留与 focal 相关的块；
  3. `--max-members` 可调。
- **验收标准**：
  1. `cg view Q5 --with-blocks` 输出体积从 28774 B 下降 > 60%（即 < 11500 B）；
  2. focal 相关成员一条不少（Q5 的 26 个引脚网络全部保留）；
  3. GND 成员列表折叠为 `(+N more)` 形式。

### 9. KB 反查
- 现状：`cg kb lookup <型号>` 可查（`SW5001` 正是 Q5/Q7 的型号，lookup 已能返回完整 pinout），但无法从型号反查"本板用了几处、分布在哪些网"。`cg kb search` 已存在（全文字段：part_number / description / ...），只是没有按引脚名过滤的能力。
- 建议：`cg kb lookup --usage-in <netlist>`（型号→本板用量/位号/所在网）、`cg kb search --pin <name>`（按引脚功能名反查型号，如 `SW2` → SW5001）。
- **验收标准**：
  1. `cg kb lookup SW5001 --usage-in fixtures/protel/FX-T268-V6-10米-5065.NET` 输出 Q5 / Q7 及其所在网络（DCIN_1 / SW1 / SW2 / SW3 / SW4 等）；
  2. `cg kb search --pin SW2` 能列出所有含 SW2 引脚的型号。

---

## 本轮范围外

- Protel `.NET` 的 lint（悬空网 / 重复位号 / 电源冲突等）——本轮不做，`cg erc` 仍只支持 KiCad `.kicad_sch`。

## 简易验收（收尾自检，一次跑完）

前置（在 circuit-graph 仓库根目录）：

```powershell
$CG  = ".\.venv\Scripts\cg.exe"
$NET = "fixtures\protel\FX-T268-V6-10米-5065.NET"
```

| # | 检查项 | 命令 | 期望 |
|---|--------|------|------|
| 1 | 拓扑提速（**冷启动**） | `Remove-Item .cg-cache -Recurse -Force -EA SilentlyContinue; Measure-Command { & $CG view C9 $NET \| Out-Null }` | < 1.5 s |
| 2 | 缓存生效（**热启动**） | `Measure-Command { & $CG view C9 $NET \| Out-Null }` | < 0.3 s |
| 3 | warnings 可见 | `(& $CG view C9 $NET \| ConvertFrom-Json).warnings` | 是数组（可为空），键存在 |
| 4 | 无超时模板 | `& $CG view C9 $NET --with-blocks \| Select-String "ABORTED"` | 无输出 |
| 5 | RC 滤波不再丢失 | `& $CG view C9 $NET --with-blocks \| Select-String "rc-low-pass"` | 有命中 |
| 6 | 假阳性消除 | `& $CG view Q5 $NET --with-blocks` | 不同时出现 `nmos-low-side-switch-q5` 与 `pmos-high-side-switch-q5` |
| 7 | 新基元生效 | `& $CG view C9 $NET --with-blocks` | 出现 `rc-snubber`（C9，经 R26）与 `bootstrap`（C27） |
| 8 | 对称组 | `& $CG view C9 $NET --with-blocks` | 输出 4 路开关节点对称组（SW1/SW2/SW3/SW4） |
| 9 | query 命令 | `& $CG query $NET --conn C9` | 输出 < 20 行且含 `NetC9_2` |
| 10 | 输出截断 | `(& $CG view Q5 $NET --with-blocks \| Out-String).Length` | < 11500 B（基线 28774 B） |
| 11 | KB 反查 | `& $CG kb lookup SW5001 --usage-in $NET` | 输出 Q5 / Q7 及其所在网络 |
| 12 | 回归 | `uv run pytest` | 无新增失败 |

> - 第 10 项注意：`(& $CG ...)` 在 PowerShell 里是**行数组**，`Out-String` 才是文本长度。
> - 第 1 项必须先清缓存，否则测的是热启动。
> - 第 1~11 项全绿后，再跑一次 `uv run python scripts/build_primitive_index.py` 更新基元索引。

## 完成后清理

- 上表 12 项全部通过后，删除本文件，并在 `Readme.md` 中同步移除对应的"待办 / 进行中"描述。
