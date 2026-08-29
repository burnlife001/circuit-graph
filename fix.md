# Fix

## P1: `cg view` 管线缺 `MultiUnitSplitter`

- 位置:`circuit_graph/cli.py:_parse_and_enrich`
- 现状:CLI 管线为 ValueNormalizer → NetClassifier → PinNameResolver → EdgeSemanticEnricher → TopologyRecognizer,不含 MultiUnitSplitter。LM358 等多单元器件在 cg view 里仍是 U2 整体,`cg view U2B` 报 component not found,opamp-unit-* blocks 也不会出现
- 结论(2026-08-19):**要修**。拆分是识别正确性的前提——不拆则多单元器件引脚语义查不准、`--with-blocks` 在多单元板上残缺;整件 designator(U2)从 view 消失是交互问题,不是不修的理由

### 方案

1. `_parse_and_enrich` 管线最前加 `MultiUnitSplitter`(priority=10,与测试管线一致)
2. `cg view U2` 找不到时检查 `U2A/U2B/...` 拆分单元;存在则报错并提示单元列表,如:
   `Component U2 not found. 已拆分为多单元: U2A(电源), U2B, U2C — 请用 cg view U2B 查看`
3. 测试(add-primitives.NET):
   - `view U2` → 提示单元列表
   - `view U2B --with-blocks` → 命中 `opamp-unit-differential-amplifier-u2b`
