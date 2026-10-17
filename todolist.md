# TODO — circuit-graph 遗留问题清单

> 来源：2026-10-17 审查建议执行（6 阶段）收尾时确认未处理的问题。
> 每条含验收标准；完成后从清单移除并恢复对应的 CI 门禁/skip。

## P0 — 2 处真实 latent bug（缺导入，运行到即炸）

### 1. `circuit_graph/layout/unit_layout.py` 缺 `Any` 导入
- 现象：`unit_layout.py:28` 类型注解 `dict[str, Any]` 但模块未导入 `Any`（F821）。
- 现状：pyproject `[tool.ruff.lint.per-file-ignores]` 中临时豁免（带 TODO 注释）。
- 修法：补 `from typing import Any`（检查同文件是否还有其他未导入名）。
- **验收标准**：
  1. 删除 pyproject 中该文件的 per-file-ignore；
  2. `uv run ruff check --select F821,F823 circuit_graph` 通过；
  3. `uv run pytest tests/layout -q` 不新增失败。

### 2. `circuit_graph/skills/kicad/scripts/kicad_types.py` 缺 `NetlistQueries` 导入
- 现象：`kicad_types.py:73` 引用未定义的 `NetlistQueries`（F821）。
- 现状：同上，per-file-ignore 临时豁免。
- 修法：确认 `NetlistQueries` 的真实出处（大概率是 `circuit_graph.query` 或布局模块里的类），补导入或改为字符串注解 + `TYPE_CHECKING`。
- **验收标准**：同第 1 条（删豁免 + F821/F823 门禁绿 + 相关测试不新增失败）。

## P1 — 4 个 base commit 即失败的测试（CI 中 deselect，见 `.github/workflows/ci.yml` 注释）

### 3. `tests/erc/test_extractor.py::test_extract_lm321_groups_power_rails`
- 现象：断言失败（本地 Windows、HEAD 上即可复现，非环境缺失类）。
- 排查方向：erc extractor 对 LM321 原理图的 power rail 分组逻辑与断言期望不一致。
- **验收标准**：用例通过；从 ci.yml 的 deselect 清单移除；全量 `uv run pytest -q` 失败数 -1。

### 4. `tests/layout_check/test_integration.py::test_lm321_default_rule_violations_match_snapshot`
- 现象：违规数与 `test_integration.snapshot.json` 基线不一致。
- 排查方向：确认是规则/解析行为漂移（需更新 snapshot 并说明原因）还是真回归（修代码）。更新 snapshot 用 `--baseline` 机制重新录制。
- **验收标准**：同上（用例通过 + 移出 deselect）。

### 5. `tests/parsers/test_kicad_sch.py::test_lm321_has_expected_components`
- 现象：组件断言失败。
- 排查方向：kicad_sch parser 对 lm321 fixture 的组件提取与期望清单漂移。
- **验收标准**：同上。

### 6. `tests/test_primitives.py::test_primitive_index_is_up_to_date`
- 现象：primitives 索引与磁盘模板不同步。
- 排查方向：大概率只需按测试提示重新生成索引（找 primitives 索引生成脚本）；若生成后仍失败再查排序/编码问题。
- **验收标准**：同上；若确为重新生成索引即可修复，提交时附带说明索引为何漂移。

## P2 — 存量 ruff 风格问题约 950 条（未入 CI 门禁）

### 7. 分批清理存量 lint
- 现状：CI 仅门禁 `F821,F823`；`E/I/N/W/F` 全量规则下约 950 条发现（多为 E501 超长行、I001 import 排序、F841 未用变量）。
- 建议顺序：先 `ruff check --fix`（安全自动修复，约 117 条）→ 再按目录分批手工清理（每批一个 commit）→ 最后把 CI 门禁扩到完整规则集。
- **验收标准**：
  1. `uv run ruff check circuit_graph` 全绿；
  2. ci.yml 的 ruff 步骤改为 `uv run ruff check circuit_graph`（去掉 `--select` 限制）；
  3. 每批清理后全量 pytest 不新增失败。

## 完成后清理

- 以上全部完成后，删除本文件，并在 `Readme.md` 测试段移除"4 个 deselect / 950 条 ruff"的描述。
