# 模板共建下一阶段计划

创建：2026-10-31  
前置提交：`cf7d4ae`（全量 749 passed / 4 skipped / 0 failed）  
资产说明：`docs/template-system-assets.md`

> **2026-11-01 已全部执行完毕**（P0–P5 + 全分支终审修复，提交链 `566d782→49082ea`，
> 收尾全量 774 passed / 0 failed）。终审遗留 minor 见 ledger（已随会话归档）；
> 下一轮建议起点：T193 工程师确认清单（`docs/t193-baseline.md`）、
> LiveMatchSession 尊重 strict_pin_matching 的架构修法、KB 补录 T193 未知型号。

## 目标

让普通硬件工程师只用自然语言就能完成：

```text
指出元件用途 → 基准落库 → 找到差异 → 补通用模板 → 全量验证
```

本阶段优先提高**答案正确性与模板防误报能力**，不追求模板数量。新模板要有真实板卡需求、正例与反例，不为 coverage 数字硬凑。

## 下个会话启动方式

在仓库根目录直接输入：

```text
执行 docs/template-system-next-plan.md，从 P0 开始；严格按验收标准推进。
```

先读 `.claude/CLAUDE.md`、本文、`docs/template-system-assets.md`。默认板卡：

```bash
NET="fixtures/protel/FX-T268-V6-10米-5065.NET"
```

运行命令使用 `.venv/Scripts/python.exe` / `.venv/Scripts/cg.exe`，git-bash 路径用正斜杠。

## P0：冻结基线，重测真实差异

1. `git status --short`，先识别是否有其他会话的未提交修改；不要 `git add -A` 盲提交。
2. 全量 pytest，必须维持 0 failed。
3. 运行：

```bash
.venv/Scripts/python.exe .claude/skills/cg-align/scripts/coverage_test.py "$NET" --json
```

4. 把 `mismatched` 分成三类，而不是直接追求对齐率：
   - **同义措辞**：如“RC 吸收网络（snubber）电容”与“RC 吸收电路”，功能其实相同；
   - **答案升级**：新输出比旧基准多出正确网络/参数/型号，例如 R100、R101、R102；
   - **真实分歧**：拓扑或功能定性不同，需修模板或请用户裁定。

**完成标准**：输出差异清单及每条分类，不用放宽断言或删测试让报告变绿。记录本轮真实对齐率，不把“措辞更详细”当成功能错误。

## P1：让对齐报告区分“语义不同”与“更详细”

当前 `coverage_test.py` 只做剥角色后缀后的文本比较，恢复详细答案后会出现大量“基准更短、当前更具体”的 mismatch。

实施：
- 保留现有严格 diff 作为原始证据；
- 新增分类 `refined`：基准与当前属于同一模板/功能，当前只是补充了网络、配对元件、型号、参数；
- 同义措辞不自动判对：交 agent 依据模板语义和用户答案复核；
- 结论同时报告 `aligned / refined / mismatched`，不以文本包含关系单独认定语义相同；
- R2 这类从“下拉”改为“采样”的变化必须保持真实 mismatch，不能归为 refined。

验收：
- R100/R101/R102 不再被当成“需要修代码”的差异；
- R2 的基准仍为 `pull-down-resistor`，必须标为需要更新基准的真实分歧；
- 全量 pytest 0 failed。

## P2：按真实分歧更新基准，而非让基准拖住正确答案

使用 `.claude/skills/cg-align/SKILL.md` 的流程 B：

1. agent 将确认后的正确功能整理为 `input.json`；
2. `nl_to_jsonl.py ... --dry-run` 检查新增/更新/冲突/错误；
3. 对已有 `session-correction` 的条目，只有用户明确确认才以 `source=human` 覆盖；
4. 写回前自动 `.bak`；写回后再跑 `coverage_test.py`。

候选：
- R2：基准从错误的 `pull-down-resistor` 更新为低边电流采样；模板/判据应体现当前 `shunt` 后处理。
- R100/R101/R102：保留用户确认的功能语义，补充配对元件、网络、增益/fc 到 `detail`，不必把 function 写成完整输出标题。
- U4：明确 `JW5065 同步降压变换器` 与通用 `buck-converter` 的专用优先关系，更新基准 block，而不是把专用模板改回通用模板。

验收：基准不丢失任何已确认的用户语义；无空 `block` 覆盖；全量 pytest 0 failed。

## P3：修两个已观察到的模板问题

### P3.1 Q5 也是 SW5001，但未命中全桥模板

- 比较 `cg role Q5 "$NET"`、`cg role Q7 "$NET"` 的 IR 引脚名、value、位号类型与模板严格匹配条件。
- 优先复用 `primitives/power/sw5001-full-bridge.json`，不为 Q5 复制一份专用模板。
- 检查 strict value / 引脚约束是否只适配了 Q7 的表示方式；以 datasheet/KB 为证据，不靠型号字符串猜引脚功能。
- 增加 Q5 正例与非 SW5001 反例；若需改 schema 或解析器，先说明最小改动。

### P3.2 U2（MM32F014X MCU）曾被误报为运放单元

- 先复测当前 `cg role U2 "$NET"`，不要沿用旧输出下结论。
- 若仍误命中，补“MCU 不得当运放”的负例；优先使用引脚角色/`expected_pin_category` 约束。
- KB 无 pinout 时必须保持降级/未命中，不得为覆盖率恢复无依据猜测。

**P3 验收**：Q5/Q7 都能给出有证据的全桥定性；U2 不乱报运放；模板索引已重建、每个相关模板有 fixture CASE、全量 pytest 0 failed。

## P4：把普通工程师动线写到 Readme

只写普通用户能执行的三步，不堆 schema 技术细节：

1. 直接说：`R95 是 I2C SDA 上拉电阻，接到 5V2`；
2. agent 校验并告诉用户哪些答案被更新、哪些仍需确认；
3. agent 补模板并跑测试，最后给出新的全板对齐报告。

链接 `docs/template-system-assets.md` 与 `.claude/skills/cg-align/SKILL.md` 给想深入的人。技术细节仍放 `docs/architecture.md`，不塞进 Readme。

验收：一个没读过源码的工程师能照 Readme 发出第一条纠错，不需要手工编辑 JSONL、不需要懂 Python。

## P5：建立下一轮真实板卡反馈

- 选择一块新的 `.NET`，先用 `cg role --all` 与 coverage_test 建立基线，再为该板创建 `<板名>.answers.jsonl`。
- 优先录入工程师能明确确认的 5–10 个元件，不批量相信 cg-role 自动答案。
- 从新板反推模板时，必须同时保留 FX-T268 的回归；若新模板只适配一块板，说明它还不够通用。

## 全程约束

- 修改模板必做：重建索引、补 fixture CASE、全量 pytest。
- 不改 IR 原始信息；不把 KB 缺引脚当成已知；不靠行业惯例猜元件功能。
- 不删除/放宽断言；行为确实改善时才更新断言并说明原因。
- 每个小任务：Readme（若有普通用户可见变化）+ git commit + git push；提交前明确列出范围，避免混入并行 WIP。
