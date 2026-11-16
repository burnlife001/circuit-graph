# CLAUDE.md — circuit-graph

# 探索代码用cbmm-mcp,比Grep|Glob|Read强多了

## 项目定位
- 一行命令看穿电路是干什么的
- 长期目标：可扩展的电路理解平台，支持 AI 识别电路结构
- **当前阶段**：模板系统完善——让普通硬件工程师能参与模板建设（自然语言纠错 → 落库 → 模板 → 对齐测试闭环），不需要懂 Python

## 架构原则
- Python 是 circuit-graph 的唯一实现语言
- 原有 IR.json 100% 精确，不变形、不丢失信息
- Python 项目使用 uv + pytest，依赖见 pyproject.toml

## 三层资产模型（新增/移动文件先想清属于哪层）
| 层 | 内容 | 位置 | 变更方式 |
|---|---|---|---|
| 模式层（任意 .net 通用） | primitives/*.json + schema.json | 仓库 `primitives/` | 改模板 = 改产品，走测试 |
| 基准层（单板 oracle） | `<板名>.answers.jsonl` | fixture 旁 sidecar，如 `fixtures/protel/FX-T268-V6-10米-5065.answers.jsonl` | 人工/会话确认，随板沉淀 |
| 派生层（可再生成） | coverage 报告、questions-by-block | `output/`（不入库） | 命令现算 |

对齐测试 = 模式层跑该 .net，输出与基准层逐元件 diff；不一致要么修模板（模式层受益所有板），要么修答案（基准层）。

## 模板修改三件套（缺一不可）
1. 改/加 primitives 模板 → 重建索引 `scripts/build_primitive_index.py`
2. 新模板必须配 fixture CASE（`tests/test_primitives_on_fixtures.py`，`test_every_primitive_has_a_fixture_case` 强制）
3. pytest 无新增失败（基线先行；禁止删除或放宽既有断言，行为确实变更时更新断言到与正确行为一致并说明理由）

## 硬件工程师贡献工作流（cg-align 技能）
工程师只需自然语言说"XX 是 YY"，其余由 agent 完成：
1. **落库**：`nl_to_jsonl.py` 把答案写进基准层（权威优先级 human > session-correction > cg-role；冲突拒覆盖）
2. **对齐**：`coverage_test.py` 出对齐报告（对齐率/block-drift/mismatch 清单）
3. **补模板**：未命中的走 `propose_template.py` 出草稿 → agent 审定（防误报优先于防漏报：宁可未命中，不要乱定性）→ 模板三件套

## 调研工作区
- 代码调研统一在 `E:/__electric/__research`
- CircuitOps 本地副本：`E:/__electric/__research/CircuitOps`
- datasheet目录:.env文件中的DATASHEET_DIR
- 主项目仓库不混入调研性克隆或临时构建

## 回答风格(答案前置)
- "X是干什么的" / "d2的作用" 等查询 → 第 1 句必须给**功能定性** + **关键型号/位置**
- 引脚表、邻居 net、拓扑细节放后面，不要前置
- 不要让用户读半天才找到答案(反馈:看完没立即看到"峰值检波"几个字)

## 注意
- Readme.md只记录给普通用户看的东西(不记录技术参数)，技术参数记录到docs/architecture.md中
- 每完成一个小任务都 `修Readme.md + git commit + git push`

## 用户测试约定(默认用"fixtures/protel/FX-T268-V6-10米-5065.NET"进行分析)
例如：用户输入:R100是干什么的,就使用cg role <id> <file>`（一条命令出结论，fallback 或存疑时才走 cg query 手动路径，还存疑调用cg技能）

## git 纪律（事故教训）
- `git add -A` 前必须看 `git status` 确认范围：仓库常有刻意保持未提交的 WIP，一刀切会污染提交
- 修错用新提交还原，不用 amend/reset 历史
