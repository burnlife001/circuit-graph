# crash.md 修复 TODO — cg role R96 StopIteration

## 验证结论：问题属实 ✅

| crash.md 论断 | 验证结果 |
|---|---|
| `net_classifier.py:68` 正则 `^(C?GND\|...)` 误匹配 CGND | ✅ 属实（第 68 行原样存在） |
| `role.py:469` `_rule_shunt` 中 `next()` 无防护 | ✅ 属实（第 460-469 行） |
| R96 = 0.05R（1206W），两 pin 分别在 GND 和 CGND | ✅ 属实（.NET 第 1474/2372/2421/2433 行） |
| 板上有 GND、PGND、CGND 三个不同的地网络 | ✅ 属实（.NET 第 1887/2291/2421 行） |

触发链：R96 阻值 ≤0.1Ω → 进入 shunt 规则 → GND 与 CGND 都被分类为 GND → `next()` 找不到非 GND net → `StopIteration` 崩溃。

## 修复任务

- [x] **T1 收紧地网络分类正则**（`circuit_graph/enrichers/net_classifier.py:68`）
  - 把 `^(C?GND|PGND|AGND|DGND|SGND)` 改为 `^(GND|PGND|AGND|DGND|SGND)`
  - CGND 是否为地需要结合板级语义判断：本板 GND/CGND/PGND 是三个独立 net，CGND 不应并入 GND
  - 待确认点：若其他板子上 CGND 与 GND 实际等电位，分类为 UNKNOWN 是否影响其他规则 → 跑全量回归确认
  - ✅ 已完成：正则收紧；连带修复 EdgeSemanticEnricher（`_classify_role` 按名字回退，UNKNOWN 地网仍标 `role=gnd`，保证带 `expected_role=gnd` 的模板不回归）

- [x] **T2 `_rule_shunt` 加防御处理**（`circuit_graph/role.py:467-469`）
  - 当 `to_gnd` 为真但所有 pin net 都是 GND 时，不再 `next()` 崩溃
  - 改为回退为"串联分流器"分支（ev_net 显示两端 net 名），或给 `next()` 提供 default 并显式标注双端接地
  - 原则：规则引擎任何一条规则都不允许抛未捕获异常
  - ✅ 已完成：`next()` 带 default=None；双端全 GND 时回退为「串联分流器，双端接地」

- [x] **T3 回归测试**
  - 新增 pytest：构造双 GND 类 net（GND+CGND）的毫欧电阻用例，断言不抛异常且给出 shunt 角色
  - 新增 net_classifier 用例：`CGND`、`GND`、`PGND` 的分类断言
  - 全量回归：fixtures 下所有 .NET 跑 `cg role`，无新增 crash
  - ✅ 已完成：`test_r96_gnd_cgnd_bridge_is_shunt_without_crash`（真实 fixture）+ `test_net_classifier_gnd_family`；全量 696 passed / 4 skipped

## 验收标准

1. `cg role R96 fixtures/protel/FX-T268-V6-10米-5065.NET` 不抛异常，输出"电流采样电阻"角色结论
2. `pytest` 全绿（含新增 2 组用例）
3. 现有 fixtures 全量跑 `cg role` 与修复前 diff：除 R96 由 crash→正常输出外，其余元件结论不变
4. `CGND` 不再被分类为 `NetType.GND`；`GND`/`PGND` 分类保持不变
