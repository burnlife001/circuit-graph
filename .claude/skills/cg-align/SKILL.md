# cg-align — 元件答案对齐工作流

对齐三层资产里的**基准层**（`fixtures/protel/<板名>.answers.jsonl`）与**当前 cg 输出**：
找出哪些元件的定性对不上、把人工纠正落进基准、给没命中的元件补 primitives 模板。

**触发条件**：用户说「对齐」「纠正元件答案」「补模板」「coverage」「答案对不上」「基准过期」，
或要复盘某块板的 `cg role` 输出与既有答案的差异。

## 三层资产（不要越界）

| 层 | 位置 | 谁能改 |
|---|---|---|
| 模式层 | `primitives/*.json`（通用模板） | 改模板要重建索引 + 补 CASE |
| 基准层 | `fixtures/protel/<板名>.answers.jsonl`（单板 oracle） | 只经 `nl_to_jsonl.py` 写 |
| 派生层 | `output/`（快照） | 不入库 |

## 硬规则

- 🚫 **不读 `.NET`**：电路数据一律经 `cg`（`cg query` / `cg role` / `cg primitives coverage`）。
- 🚫 **不用脚本调 LLM、不联网**：三个脚本都是纯确定性逻辑（校验 / diff / 生成草稿）；
  语义判断（这条答案对不对、模板该叫什么名）由 agent 在本流程里做。
- 🚫 **不改既有测试断言、不删测试**：pytest 失败数不得增加（当前基线 **0 failed / 749 passed / 4 skipped**，2026-10-31 全量实测；若后续基线变化以最新全量结果为准）。
- 🚫 **不直接手改 `fixtures/protel/*.answers.jsonl`**：走 `nl_to_jsonl.py`（验收阶段用 `--dry-run` 或 `--answers` 指到 `/d/tmp` 副本）。
- **答案冲突权威优先级**：`human > session-correction > cg-role`。
  基准行已是 `human`/`session-correction` 时，非 `human` 来源的新答案**拒绝覆盖**（记 conflict，exit 1）。
- **匹配上 ≠ 定性对**：判据是 `function`（答案文本）对齐，不是模板命中。
- **防误报优先于防漏报**：宁可未命中，不要乱定性；模板必须补反例约束（`expected_role` / `expected_ic_pin` / `expected_pin_category`）。
- 改模板后必做三件事：① `python scripts/build_primitive_index.py` 重建索引
  ② 在 `tests/test_primitives_on_fixtures.py` 补 fixture CASE（每个模板至少一条）
  ③ 全量 pytest 无新增失败。

## 流程 A —— 对齐体检（先跑这个）

```bash
python .claude/skills/cg-align/scripts/coverage_test.py fixtures/protel/<板名>.NET
python .claude/skills/cg-align/scripts/coverage_test.py fixtures/protel/<板名>.NET --json
python .claude/skills/cg-align/scripts/coverage_test.py fixtures/protel/<板名>.NET --threshold 80
```

内部：`cg role --all <net> --format json` + `cg primitives coverage <net> --format json`，与 answers.jsonl 逐位号 diff。

报告六节 + 一行结论：

| 节 | 含义 | 接下来做什么 |
|---|---|---|
| `aligned` | 当前 function 与基准一致 | 无需动（但注意 block-drift 节可能仍 flagged 换模板） |
| `refined` | 同一模板下当前只是补充网络/配对/型号/参数（block 相同、文本不同） | **agent 必逐条复核**：模板语义是否被改过、基准 block 是否继承自历史误命中；确认同功能后才算过 |
| `mismatched` | 两边都有、function 不同且 block 不同/未知 | 逐条判断谁对 → 走流程 B |
| `unmatched-in-answers` | 网表里的元件在基准里没有任何记录（待补答案） | 走流程 B 补答案 |
| `block-drift` | 基准 `block` 与当前 coverage 的模板 id 不同 | 多半是模板变了，回写 block |
| `coverage` | 命中模板数 / 未命中数 | 未命中的走流程 C |
| `stale-in-answers` | 基准里有、网表里已无此位号 | 人工确认后删（脚本不自动删） |

结论行 `对齐率 X/Y (Z%)`：X=aligned，Y=基准里有的元件数，Z=X/Y*100；
另报 `有效对齐率(含 refined)` =(aligned+refined)/Y——refined 不算真实分歧，
**但不等于免审**，agent 复核职责见上表。`--threshold N` 只对严格对齐率生效。

## 流程 B —— 把人工纠正落进基准

### 第 1 步：agent 整理成中间格式

agent 从人类自然语言/对话里提取，写成 `input.json`（UTF-8）：

```json
{
  "entries": [
    {
      "designator": "R95",
      "function": "I2C SDA 线上拉电阻",
      "detail": "上拉到 5V2",
      "block_hint": "pull-up-resistor",
      "source_note": "2026-10-31 用户口述：这是 I2C 总线的 SDA 上拉",
      "source": "human",
      "session": "可选，会话 id"
    }
  ]
}
```

字段说明：

| 字段 | 必填 | 含义 |
|---|---|---|
| `designator` | ✅ | 位号（大小写不敏感匹配网表里的真实位号） |
| `function` | ✅ | 功能定性一句话（要与 answers.jsonl 的 function 同口径） |
| `detail` | | 详细说明；为空时保留基准里的旧 detail |
| `block_hint` | | primitives 模板 id；必须是已知模板 id，否则该条目被拒 |
| `source_note` | | 备注，仅供人看，不入库 |
| `source` | | `human` / `session-correction` / `cg-role`；不写则按 auto 规则推断（见下） |
| `session` | | 会话标识，写进基准行 |

### 第 2 步：dry-run

```bash
python .claude/skills/cg-align/scripts/nl_to_jsonl.py fixtures/protel/<板名>.NET /d/tmp/align_input.json --dry-run
```

输出四组：`[新增]` `[更新]` `[冲突]` `[错误]`，加一行统计。
**冲突或错误 → exit 1**，报告已打印，先解决再写。

### 第 3 步：写回

```bash
python .claude/skills/cg-align/scripts/nl_to_jsonl.py fixtures/protel/<板名>.NET /d/tmp/align_input.json
```

写回前自动留 `<answers>.bak` 备份，再原子替换。
验收阶段不许真改 `fixtures/protel/*.answers.jsonl` —— 用 `--dry-run`，或 `--answers D:/tmp/answers_copy.jsonl` 指到副本。

### source 判定（auto）

- entry 里写了 `source` → 用它；
- 没写：**基准里没有该位号 → `human`**（新答案）；**基准里已有 → `session-correction`**（对既有答案的修订）。
- `--source human|session-correction|cg-role` 强制本次全部条目；
- `--keep-source`：更新已有行时保留其原 source。

冲突规则：基准行 source ∈ {`human`,`session-correction`} 且本次 source ≠ `human` → **拒绝覆盖**，记 conflict、exit 1。
想覆盖权威基准，必须显式声明人工确认：entry 写 `"source": "human"` 或 `--source human`。

`block_hint` 是未知模板 id 时：该条目被拒（记 error，不写入、**也不会清空既有 block**），
宁可空着也不写错模板 id。确认该答案**没有**对应 primitives 模板时，可显式写
`"(unmatched)"`（如走 shunt 规则路径的 R2），基准如实记录未命中。

## 流程 C —— 给未命中的元件补模板

```bash
python .claude/skills/cg-align/scripts/propose_template.py fixtures/protel/<板名>.NET L2 L3 --function "buck 变换器输出电感"
```

脚本做：校验位号 → `cg query --comp` 取引脚→网络 → 共性网络聚类出 signals 候选 →
参照 `primitives/schema.json` 与 2-3 个同类现有模板 → stdout 打印草稿 JSON + 参照模板 + checklist。
**脚本不写文件**，id 是 `draft-<slug>` 占位。

agent 要补的（checklist ①②）：

1. **审 id / name / name_pattern 语义** —— 草稿的 `name_pattern` 是从实际网络名反推的精确匹配，
   换成语义化正则（如 `^(VIN|VCC|VBUS)$`）才可能匹配别的板；id 改成语义 id（如 `buck-output-inductor`）。
2. **补反例约束防误报** —— `expected_role`（电源/地/信号）、`expected_ic_pin`（该网是否必须接 IC 引脚）、
   `expected_pin_category`（引脚类别）。没有反例约束的模板会到处乱命中。

落库三件事（缺一不可）：

```bash
# 1) 写到 primitives/<类目>/<id>.json，然后重建索引
python scripts/build_primitive_index.py
# 2) 在 tests/test_primitives_on_fixtures.py 补一条 fixture CASE（每个模板至少一条）
# 3) 全量 pytest，无新增失败
.venv/Scripts/python.exe -m pytest -q
```

## 文件

| 文件 | 职责 |
|---|---|
| `scripts/align_common.py` | 共用工具：绝对路径调 `cg`、读/写 answers.jsonl、模板 id 集合、UTF-8 输出 |
| `scripts/nl_to_jsonl.py` | 流程 B：NL 答案 → 基准层（校验位号、冲突规则、备份+原子写回） |
| `scripts/coverage_test.py` | 流程 A：当前 cg 输出 vs 基准的逐位号 diff 报告 |
| `scripts/propose_template.py` | 流程 C：未命中元件的模板草稿（只打印，不写文件） |
