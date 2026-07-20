# CLAUDE.md — circuit-graph

# 探索代码用cbmm-mcp,比Grep|Glob|Read强多了

## 项目定位
- 一行命令看穿电路是干什么的
- 长期目标：可扩展的电路理解平台，支持 AI 识别电路结构

## 架构原则
- Python 是 circuit-graph 的唯一实现语言
- 原有 IR.json 100% 精确，不变形、不丢失信息
- Python 项目使用 uv + pytest，依赖见 pyproject.toml

## 调研工作区
- 代码调研统一在 `E:\__electric\__research`
- CircuitOps 本地副本：`E:\__electric\__research\CircuitOps`
- datasheet目录:.env文件中的DATASHEET_DIR
- 主项目仓库不混入调研性克隆或临时构建

## 实施工作流
- 当前处于 OpenSpec 多 change 并行开发阶段。
- 新会话从 `openspec/changes/` 的 active changes 继续，按 OpenSpec 流程创建、归档。
- Active changes:
  - `2026-07-01-knowledge-base-redesign` — 元件型号知识库 SQLite 迁移与收尾
- **归档 change 只用 `openspec archive <name>`**：它会把 delta specs 合并进 `openspec/specs/`。🚫 不要手动 `git mv` 或用 `--skip-specs` 归档（除非确无 delta specs），否则主 specs 不会更新、`openspec/specs/` 形同虚设。归档后用 `openspec validate --specs --strict` 确认。
- 每完成一个小任务都 `修Readme.md + git commit + git push` 

## 用户测试约定(默认用"fixtures\protel\FX-T268-V6-10米-5065.NET"进行分析)
- 用户输入:测试：R100是干什么的,就使用cg view <id> <file> [--hops N] [--pretty]`
