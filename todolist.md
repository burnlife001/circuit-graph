# TODO — circuit-graph 遗留问题清单

> 来源：2026-10-17 审查建议执行（6 阶段）收尾时确认未处理的问题。
> 每条含验收标准；完成后从清单移除并恢复对应的 CI 门禁/skip。
>
> 进度（2026-10-22）：P0 两项 latent bug 已修（提交 9cdb2f0）；P1 第 3/4/5 条测试已修复并从 ci.yml deselect 移除；第 6 条已重新生成索引修复——漂移原因仅为索引头部的生成日期。剩余：P2 ruff 存量清理。

## P1 — 存量 ruff 风格问题（未入 CI 门禁）

### 7. 分批清理存量 lint
- 现状：CI 仅门禁 `F821,F823`；`E/I/N/W/F` 全量规则下当前约 828 条发现（多为 E501 超长行、I001 import 排序、F841 未用变量）。
- 建议顺序：先 `ruff check --fix`（安全自动修复，约 117 条）→ 再按目录分批手工清理（每批一个 commit）→ 最后把 CI 门禁扩到完整规则集。
- **验收标准**：
  1. `uv run ruff check circuit_graph` 全绿；
  2. ci.yml 的 ruff 步骤改为 `uv run ruff check circuit_graph`（去掉 `--select` 限制）；
  3. 每批清理后全量 pytest 不新增失败。

## 完成后清理

- 以上全部完成后，删除本文件，并在 `Readme.md` 测试段移除 ruff 存量问题相关的描述。
