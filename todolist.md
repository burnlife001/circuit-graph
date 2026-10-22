# TODO — circuit-graph 遗留问题清单

> 来源：2026-10-22 T268-V6（`fixtures/protel/FX-T268-V6-10米-5065.NET`，207 元件）电路分析实战。
> 每条含验收标准；完成后从清单移除并恢复对应的 CI 门禁/skip。
> 进度（2026-10-22）：新建本清单，8 条待办（P0×3 / P1×3 / P2×2）。

## P0 — 拓扑识别的性能与正确性

### 1. 拓扑匹配候选集预筛 + 可配置超时
- 现状：`cg view` / `cg export graph` 端到端 **12.6 s**，其中 ~99% 落在 `TopologyRecognizer`（实测 7.7~12.1 s，其余阶段各 <0.01 s）。`enrichers/topology_recognizer.py:50` 对每个模板硬编码 `timeout_seconds=2.0`，实测 4 个模板在 2 s 内 abort（matches=0）：`sallen-key-low-pass` / `rc-low-pass` / `rc-high-pass` / `lc-pi-filter`；放宽到 60 s 后 `rc-low-pass` 用 4.94 s 找到 **106 个匹配**、`rc-high-pass` 4.85 s 找到 14 个 —— 也就是说当前整类 RC 滤波功能块被**静默丢弃**。
- 建议：
  1. 用模板的 `signals[].expected_role` / `name_pattern` 先把候选 net 缩到几十个，再在候选集上跑 VF2，而不是全图两两搜；
  2. 超时改为可配置（CLI `--topology-timeout`，默认 10 s），预筛后实际用不满；
  3. 加匹配数上限 `max_matches`，先按 role 分组再评分。
- **验收标准**：
  1. `cg view C9 fixtures/protel/FX-T268-V6-10米-5065.NET` 端到端 < 1.5 s；
  2. 默认配置下 `rc-low-pass` / `rc-high-pass` 不再出现 aborted；
  3. 被跳过的模板能在命令输出里被看见（配合第 2 条）。

### 2. 透出 enricher warnings（结果可信度底线）
- 现状：`cli/app.py:474` 的 `_parse_and_enrich` 只返回 `.ir`，`run_enrichers` 产出的 warnings 全部丢弃；`cg view` / `cg export graph` 均不输出。实测中 4 个模板超时就是这样被静默吞掉的，使用者无法判断结果是否完整。
- 建议：`_parse_and_enrich` 改为返回 `(ir, warnings)`；`cg view` / `cg export graph` 在 JSON 顶层加 `"warnings": [...]`，同时 stderr 打印一行摘要。
- **验收标准**：
  1. 人为制造一次模板超时，命令 JSON 里能看到 `TOPOLOGY_MATCH_ABORTED`；
  2. 正常网表下 `warnings` 为空数组（不是缺键）。

### 3. IR 解析缓存
- 现状：每次 `cg view` 都重新 parse + 跑完整 enricher 管线（12 s），同一网表的重复查询全部重算。
- 建议：缓存键 = 网表内容 sha256 + enricher 版本号 + primitives 库 hash；缓存 IR JSON 到 `<repo>/.cg-cache/`，`_parse_and_enrich` 命中即返回。
- **验收标准**：
  1. 同一网表第二次 `cg view` < 0.3 s；
  2. 网表内容改动后缓存自动失效（不返回陈旧 IR）；
  3. 缓存目录可通过 CLI 参数指定，默认不写进版本库。

## P1 — 查询层与语义精度

### 4. `cg query` 紧凑查询命令
- 现状：只有 `cg view`（整块 ego-subgraph JSON，数 KB，含 GND 的 85 个成员等大块噪音）和 `cg export graph`（整份 JSON），**没有"回答单个问题"的入口**；实际使用被迫先 export 再写脚本查 JSON。
- 建议：新增 `circuit_graph/cli/query.py`：`cg query <file> --conn X --comp A B --net N --find STR --all --type T --format text|json`。text 输出为紧凑表格（位号 / 类型 / 参数值 / 型号 / 引脚→网络，IC 带引脚名）。
- **验收标准**：
  1. `cg query <netlist> --conn C9` 输出 C9 每个引脚所在网络及同网元件（含参数值），行数 < 20；
  2. `--json` 输出结构可被 `jq` 直接处理；
  3. 支持 `--find 4.7nf` 这类按参数值/型号的模糊检索。

### 5. 匹配假阳性治理
- 现状：Q5（26 脚 IC）同时被识别为 `nmos-low-side-switch-q5` 和 `pmos-high-side-switch-q5`；`nmos-low-side-switch` / `pmos-high-side-switch` / `rc-low-pass` 分别产生 232 / 256 / 106 个匹配；`peak-envelope-detector` 把 VBAK_A 的分压采样电路认成"峰值包络检波"。匹配爆炸既是下游耗时来源，也是功能块列表的噪音源。
- 建议：给基元补 `value_pattern`（区分去耦电容与滤波容）；switch 类基元对多脚器件加器件类型 / 引脚数约束；命中数超阈值时降级为"候选"而非功能块。
- **验收标准**：
  1. `cg view Q5 --with-blocks` 不再出现 Q5 同时是 N 沟道和 P 沟道开关；
  2. 全板功能块总数下降，且不残留低置信度噪声块；
  3. `rc-low-pass` 的 106 个匹配里，去耦电容类被参数约束排除。

### 6. 新增基元：RC 吸收 / 自举电容 / 开关节点 + 对称性检测
- 现状：现有 25 个基元里**没有** RC 吸收（snubber）、自举电容（bootstrap）、开关节点；`power/peak-envelope-detector.json` 的描述中已出现"开关节点"字样，说明这类需求早已存在。板级实测：SW5001 的 4 个开关节点各有"1nF 直通地 + 100nF 自举 + 2Ω/4.7nF 吸收对"，完全对称，但工具识别不出来。
- 建议：
  1. `primitives/protection/rc-snubber.json`：R 串联 + C 到地，`value_pattern` 锁 R≈2Ω、C 1~10nF；
  2. `primitives/power/bootstrap-cap.json`：C 接在 BST 与 SW 之间；
  3. `primitives/schema.json` 扩展"重复结构分组"，把 N 路结构相同的子电路归为一个对称组（如 SW1/SW2/SW3/SW4）。
- **验收标准**：
  1. `cg view C9 --with-blocks` 明确报告 C9 是 SW2 开关节点上的 RC 吸收电容；
  2. 四路 SW 结构被识别为一个 4 路对称组并在输出中列出；
  3. 新基元通过 `scripts/build_primitive_index.py` 索引生成与 schema 校验。

## P2 — 体验与知识库

### 7. `cg view` 输出截断
- 现状：GND 网络的 85 个成员全量打印，占输出体积大半，且与 focal 元件无关。
- 建议：degree > N（默认 20）的网络只列与 focal 相邻的成员，其余折叠为 `(+N more)`；`--max-members` 可调。
- **验收标准**：`cg view C9` 输出体积下降 > 60%，且 focal 相关成员一条不少。

### 8. KB 反查
- 现状：`cg kb lookup` 只能按型号查，无法从型号反查"本板用了几处、分布在哪些网"，也无法按引脚功能（如 `SW2`）反查型号。
- 建议：`cg kb lookup --usage-in <netlist>`、`cg kb search --pin <name>`。
- **验收标准**：`cg kb lookup SW5001 --usage-in fixtures/protel/FX-T268-V6-10米-5065.NET` 输出 Q5 / Q7 及其所在网络。

## 本轮范围外

- Protel `.NET` 的 lint（悬空网 / 重复位号 / 电源冲突等）——本轮不做，`cg erc` 仍只支持 KiCad `.kicad_sch`。

## 完成后清理

- 以上全部完成后，删除本文件，并在 `Readme.md` 中同步移除对应的"待办 / 进行中"描述。
