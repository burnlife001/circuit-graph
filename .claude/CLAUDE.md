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

## 用户测试约定(默认用"fixtures\protel\FX-T268-V6-10米-5065.NET"进行分析)
- 用户输入:测试：R100是干什么的,就使用cg role <id> <file>`（一条命令出结论；fallback 或存疑时才走 cg query 手动路径，见 .claude/skills/cg/SKILL.md）
- 每完成一个小任务都 `修Readme.md + git commit + git push`

## 回答风格(答案前置)
- "X是干什么的" / "d2的作用" 等查询 → 第 1 句必须给**功能定性** + **关键型号/位置**
- 引脚表、邻居 net、拓扑细节放后面，不要前置
- 不要让用户读半天才找到答案(反馈:看完没立即看到"峰值检波"几个字)

Readme.md只记录给用户看的东西，技术参数可以记录到docs/architecture.md中
