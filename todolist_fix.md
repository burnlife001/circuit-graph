# 查询引擎修复与演进 Todo List

来源:2026-07-20 查询引擎评估(对比 11.md 电气谓词查询)

## P0 — 独立修复(零风险,先做)

- [ ] **1. 修复 `neighbors_of` 的 O(E) 全边扫描**
  - 位置:`circuit_graph/query.py:69`
  - 现状:每次调用 `for edge in graph.edges()` 全图扫一遍建 lookup
  - 修法:改用 `graph._get_edges(node_id, "both")`(graph.py 内部已有索引)
  - 验证:现有 neighbors 相关测试全绿即可,无需新增行为

## P1 — 属性谓词(核心改进,避开 Cypher 式查询语言)

- [ ] **2. `TraversalOptions` 增加属性过滤字段**
  - 位置:`circuit_graph/ir.py:251`
  - 新增:
    - `net_types: list[NetType] | None` — 按 net 属性过滤(NetType 已存在于 ir.py:71,遍历引擎目前不看它)
    - `device_kinds: list[DeviceKind] | None` — 按元件类型过滤(DeviceKind 已存在于 ir.py:60)
  - 实现:`graph.py:_traverse` 扩展节点时加属性检查
  - 验证:fixture 网表上 BFS `net_types=[NetType.POWER]` 结果只含 power/gnd 节点

- [ ] **3. 用谓词 BFS 重写 `power_tree_recognizer` 核心上溯循环**
  - 依赖:任务 2
  - 目的:验证"recognizer = 预设谓词组合"的泛化路径,硬编码遍历逻辑减一
  - 注意:保持 `cg view --with-power` 输出 schema 不变(含 `source_kind: "unknown"` fallback 行为,见 Readme.md)

## P2 — 边语义(数据基础,为电气谓词铺路)

- [ ] **4. 定义 pin–net 边语义 schema 并挂到 `GraphEdge.metadata`**
  - 位置:`ir.py:241`(metadata 扩展位已存在,无人使用)
  - 字段:`direction`(source/load/bidirectional)、`role`(power/gnd/analog/digital/clock)、`level`(如 3V3)
  - 数据来源:KB IC 引脚类型(运放输出脚=源、输入脚=负载)+ `net_classifier` 已有分类 + 被动元件默认 bidirectional
  - 产出:enricher 管线新增 `edge_semantic_enricher`,按 priority 串行接入

- [ ] **5. 谓词遍历支持按边语义过滤**
  - 依赖:任务 4
  - `TraversalOptions` 增加 `pin_roles` / `edge_directions` 过滤
  - 解锁查询:`neighbors(U2.OUT, depth=2, role=analog)`、`drivers(net)`(net 上谁是源谁是负载,ERC "未驱动"检查可复用)

## P3 — 约束路径查询

- [ ] **6. 约束最短路径**
  - 现状:有引脚间最短路径,无"满足谓词的路径"
  - 例:USB → U2.VDD 只经 power 节点的供电路径;DC-only 路径(遍历时不穿过串联电容)
  - 依赖:任务 2(power 约束)、任务 5(DC_only 等语义约束)

## 明确不做(记录在案,避免反复讨论)

- ❌ **Cypher 式查询语言 / 自定义解析器** — 复杂度黑洞;用 Python API 谓词参数获得同等表达力
- ❌ **L0 物理层**(pad/via/走线)— 项目从网表出发,范围裁剪,当前目标不需要
- ❌ **元件行为指纹** — 长期愿景(对抗丝印打磨的逆向场景);当前 KB + JLCPCB/PDF 引脚解析路线更实用,不并行投入

## 顺序与里程碑

```
P0(任务1) → P1(任务2→3) → P2(任务4→5) → P3(任务6)
```

- 任务 1 可立即做,独立 commit
- 任务 2+3 为一个 OpenSpec change(遍历属性谓词)
- 任务 4+5 为一个 OpenSpec change(边语义)
- 任务 6 视 P1/P2 落地效果再立项

每完成一个任务:更新 Readme.md + git commit + git push(项目约定)。
