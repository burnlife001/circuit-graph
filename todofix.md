# Todo Fix

来源:2026-08-19 "R100 是什么作用"问答复盘 —— cg view 信息足够但缺 block 归属,需直接跑 enricher 才能拿到 FunctionalBlock。

## P0

- [x] **1. `cg view` 增加 `--with-blocks`**
  - 位置:`circuit_graph/cli.py`(`view` 命令 + `_build_blocks_context`)
  - 行为:输出 `blocks` 数组,列出 component_ids 与 ego 图相交的所有 FunctionalBlock(id / name / role / component_ids / description);无命中时给空数组而非缺 key
  - `--with-all` 现在等价于 `--with-power --with-comm --with-blocks`
  - 验证:`tests/test_cli.py` 新增 3 个用例(R100 命中 inverting-amplifier-u7 / 默认不带 blocks / hops=0 空列表),18/18 通过
  - 备注:此次问答真正的信息缺口不是 blocks,而是摘要脚本丢了 cg view 输出的 pin `name` 字段(PinNameResolver 已从知识库解析出 LM321 引脚功能);blocks 归属仍值得入 view,省一步 enricher 调用

## P1

- [ ] **2. `cg view` 的 `_parse_and_enrich` 管线缺 `MultiUnitSplitter`**
  - 位置:`circuit_graph/cli.py:_parse_and_enrich`
  - 现状:CLI 管线为 ValueNormalizer → NetClassifier → PinNameResolver → EdgeSemanticEnricher → TopologyRecognizer,不含 MultiUnitSplitter;LM358 等多单元器件在 cg view 里仍是 U2 整体,`cg view U2B` 会报 component not found,opamp-unit-* blocks 也不会出现
  - 风险:加入后 U2 这类整件 designator 从 view 中消失(变为 U2A/U2B/U2C),影响现有问答习惯,需先定交互(例如 view U2 时提示可查询的单元列表)
