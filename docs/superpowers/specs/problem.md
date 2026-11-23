  1. 架构层次与 Phase 编号混乱

  架构图定义了 3 层（L1/L2/L3），但实现优先级表列出了 5 个 Phase，外加一个独立的"阶段 5:
  层级组装"。层级组装在架构图中完全没有体现——它是 L4 还是后处理？读者无法从文档中判断 3 层架构和 5 个 Phase
  之间的映射关系。

  2. "端口名真实来源"自相矛盾

  - 第 91 行："端口名的首要真实来源是 cg kb pinout（来自 JLCPCB 符号库或 PDF 数据手册）"
  - 第 314 行："SPICE 模型是 L1 唯一数据源"

  JLCPCB 引脚图和 SPICE .SUBCKT 端口的命名体系不同（一个描述物理功能，一个描述仿真接口）。文档没有说明当两者冲突时谁优先
  ，也没有区分它们在数据流中的不同角色。

  3. PrimitiveTemplate 数据模型不兼容

  文档提出在原语 JSON 中新增 electrical 和 simulation 两个顶级字段，但当前 _parse_primitive() 的 _require_keys 只校验
  {"id", "name", "components", "signals"}。文档未说明：

  - 新字段是否加入 PrimitiveTemplate dataclass
  - 解析器如何处理未知字段（当前忽略，但仿真层需要它们）
  - 现有 6 个原语的向后兼容策略
  - version 从 "0.1.0" 跳到 "2.0" 的迁移路径

  4. Phase 1 不可实现

  Phase 1 描述为"直接用现有 6 基元"组装 SPICE deck，但现有原语没有
  simulation.analyses、simulation.stimulus、simulation.probes 字段。没有这些信息，Deck
  组装器不知道该生成什么分析命令和激励源。Phase 1 的实际依赖是 Phase 3 才交付的增强原语。

  5. L2 与现有匹配器集成未定义

  文档说 "当前 VF2 + primitives + scoring 保留，在其上叠加电气约束层"，但未说明：
  - 端口角色标注是在 VF2 匹配之前还是之后执行
  - 电气约束验证是 VF2 的后处理过滤还是替代 VF2 的匹配逻辑
  - 现有 scoring 权重（structural/naming/parametric/type）与新电气约束的关系

  ---
  中等问题

  6. L1→L2→L3 失败传播未建模

  如果 L1 找不到某 IC 的 SPICE 模型（国产 IC 很常见），L2 是否还尝试识别拓扑？L3
  是否跳过该功能块？风险表提到了"通用模型回退"，但数据流描述中没有体现这个分支。

  7. SpiceResult 类型缺失

  SpiceRunner.run() 返回 SpiceResult（第 258 行），但整个文档没有定义这个类型。仿真结果的数据结构是 API 契约的关键部分。

  8. SimulationDeck 可变性问题

  L1 的 SpiceModel 和 SpiceModelPort 都是 frozen=True，但 L3 的 SimulationDeck 使用可变的 list[...]
  字段。设计不一致——如果不可变性在 L1 中有价值，为什么 L3 放弃它？

  9. 32 原语的最后 8 个交付悬空

  Phase 5 名为"层级组装 + 全板仿真"，却要交付 "+8 基元"。组装和全板仿真是独立的工作量，把最后 8 个基元塞进 Phase 5
  看起来像是为了凑 32 的总数。缺少 Phase 6 或明确说明哪些基元可以推迟。

  ---
  轻微问题

  10. 无测试策略

  每个 Phase 声称"独立可测试"，但全文没有测试方法论、golden fixture
  定义或回归策略。对于仿真验证这种对正确性要求极高的系统，这不应该缺失。

  11. 端口角色标注规则的存储格式未定义

  第 193-204 行的规则是伪代码。它们是 Python 代码、JSON 配置文件、还是 DSL？版本管理和扩展机制是什么？

  12. 缺少收敛策略

  风险表将 Ngspice 收敛失败列为风险，缓解措施只是"标记为仿真未验证"。没有讨论标准收敛辅助手段：GMIN stepping、source
  stepping、自动重试放宽容差。

  13. raw_subckt 内存浪费

  SpiceModel 存储完整的 .SUBCKT
  原文。在加载整个模型库时，所有模型的原始文本都会被加载到内存，即使只需要其中一小部分。考虑延迟加载。

  14. 缺少温度和 Monte Carlo

  文档声称目标是"全仿真级精度"，但没有涉及温度扫描、器件容差、Monte Carlo 分析——这些是专业 SPICE 工作流的标准组成部分。

  ---
  总结：架构方向正确，三层 + Ngspice 的技术选型合理。但在进入 Phase 1 实施前，至少需要解决 #1（层次/Phase
  映射）、#3（原语格式兼容）、#4（Phase 1 可行性）和 #5（L2 集成方式）这四个问题。
