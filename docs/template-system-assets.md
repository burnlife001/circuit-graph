# 模板共建阶段：可复用资产总结

更新：2026-10-31（提交 `cf7d4ae`）  
目标：让普通硬件工程师用自然语言参与电路模板建设，不要求懂 Python。

## 1. 已验证的闭环

```text
工程师纠错（自然语言）
  → nl_to_jsonl.py 写入单板基准
  → coverage_test.py 对比当前 cg 输出与基准
  → 分歧：修基准措辞，或补/修 primitives 模板
  → 模板三件套：重建索引 + fixture CASE + 全量 pytest
```

这套流程把“某块板的正确答案”与“所有板都能复用的拓扑模板”分开：先保证答案诚实，再抽象通用模板，避免把一块板的网络名硬编码成普遍规律。

## 2. 三层资产

| 层 | 位置 | 用途 | 注意 |
|---|---|---|---|
| 模式层 | `primitives/*.json`、`primitives/schema.json` | 跨板匹配的通用拓扑模板 | 改模板必须走测试与索引 |
| 基准层 | `fixtures/protel/<板名>.answers.jsonl` | 某块板逐元件答案 oracle | 只经 `nl_to_jsonl.py` 写 |
| 派生层 | `output/` | 可随时重建的覆盖报告、分组快照 | 不入库，不作为判断依据 |

当前基准：`fixtures/protel/FX-T268-V6-10米-5065.answers.jsonl`，207 条元件记录，含 24 条来自用户会话纠错的答案。`source` 权威顺序：`human > session-correction > cg-role`。

## 3. 可直接使用的工具

### 批量问答与覆盖检查

```bash
.venv/Scripts/cg.exe role --all "fixtures/protel/FX-T268-V6-10米-5065.NET" --format json
.venv/Scripts/cg.exe primitives coverage "fixtures/protel/FX-T268-V6-10米-5065.NET"
```

- `role --all`：单进程、共享现场匹配上下文；207 个元件实测约 1.8 秒。
- `primitives coverage`：按识别器产出的功能块归组；洪泛降级后由 live match 命中的元件可能仍显示未覆盖，因此**不能单独用作 block-drift 判据**。

### 对齐技能 `.claude/skills/cg-align/`

| 脚本 | 作用 |
|---|---|
| `scripts/nl_to_jsonl.py` | 校验人工答案、处理来源冲突、备份后原子写入基准 |
| `scripts/coverage_test.py` | 全量 function 对齐、block-drift、缺答案/失效位号报告 |
| `scripts/propose_template.py` | 从未命中元件的实际引脚与网络生成模板草稿，不直接写文件 |
| `scripts/align_common.py` | cg 调用、基准读写、模板 id 解析、文本归一等共用逻辑 |

```bash
NET="fixtures/protel/FX-T268-V6-10米-5065.NET"
.venv/Scripts/python.exe .claude/skills/cg-align/scripts/coverage_test.py "$NET"
.venv/Scripts/python.exe .claude/skills/cg-align/scripts/coverage_test.py "$NET" --json
.venv/Scripts/python.exe .claude/skills/cg-align/scripts/propose_template.py "$NET" L2 L3 --function "buck 变换器输出电感"
```

`coverage_test.py` 的当前 block 以 `cg role --all` 的 `primitive:` / `primitive-live:` 结果为准，避免 flood/live-match 造成假漂移。比较 function 时会剥掉标题尾部 ` · Rf` 等角色后缀；角色变化仍可能形成真实的文本差异，需人工裁定。

## 4. 已恢复的“答案质量”标准

模板命中只回答“像哪种电路”，还要给工程师足够证据：

- R100/R97：反馈对角色、LM321、配对位号、同相增益 101。
- R101/R102/C82：RC 低通配对、具体输入网络、fc≈5.3kHz（R101）。
- R2：毫欧低边采样；不再被宽松下拉模板抢走。
- 双地无采样抽头：仍报“毫欧地桥 + medium”，不许冒称采样。
- R95：SDA 上拉到 5V2；恢复电源角色约束后仍正确。
- 视图：Q5 从 17,053 降至 11,265 字符，未提高 11,500 上限；按相关通道去重。

实现位于 `circuit_graph/role.py`：模板选择后，通过后处理补充数值与关键证据；没有恢复旧手写规则整体优先的架构。

## 5. 测试是模板共建的安全网

当前基线：**958 passed / 4 skipped / 0 failed**（2026-11-03 全量实测；T193 补三模板 + 收紧 series_signal / npn-low-side-switch 后）。

- 不删、不放宽既有断言。
- 内部 rule id 可以随模板路径迁移，但功能、参数、配对关系、负例与降级语义必须保留。
- 修改模板三件套：
  1. `.venv/Scripts/python.exe scripts/build_primitive_index.py`
  2. 新模板在 `tests/test_primitives_on_fixtures.py` 补 fixture CASE（每个模板至少一条）
  3. `.venv/Scripts/python.exe -m pytest -q` 无新增失败
- 负例优先：宁可未命中，不把不确定的电路说死。

## 6. 模板设计的已有经验

- `expected_pin_category`：拓扑相同但 IC 引脚语义不同，例如运放反馈对与分压器。
- `expected_ic_pin`：只依据网表是否接 IC 引脚，适合 KB 缺 pinout 的 MCU；例如送 ADC 的信号分压。
- `expected_connector_pin`：同理，只依据网表是否接连接器引脚；用于"串口线限流电阻→连接器"这类
  拓扑与"IC→浮空网"完全相同、只能靠连接器区分的结构。
- 模板引脚名（C/E/B 与 D/S/G）会与 KB 解析出的引脚名对角色：8050 的脚叫 B/E/C、
  AO3400 叫 G/S/D，两者不会互相冒充。代价是**引脚接法本来就不对的匹配会被拒**
  （例如把集电极电阻当成基极电阻、把 BJT 当 MOSFET 匹配）——这是纠错，不是漏判。
- 通用/专用模板会重复命中；`cg role` 已有具体度与覆盖集选择，`cg view --with-blocks` 还会按相同元件集合及相关通道去重。新增模板时应检查是否又制造重复块。
- 名称匹配只是线索，`expected_role` 等约束是防误报地基；删除约束前必须有替代负例。
- 新网络命名规律先写入实际模板与反例测试，不将单板特例当通用规则。

## 7. 模板方法论的外部参考

`E:/__work/__longMem/session-miner/NOTE-session-miner-template-method-2026-10-31.md`

核心同样适用于 primitives：模板从真实纠错反推；每个字段应有反例/激励案例；证据与决策归属是一等信息；增量演进、弃用不直接删除。

## 8. 下一阶段起点

见 `docs/template-system-next-plan.md`：先重测当前对齐率并清理措辞型假差异，再处理 Q5/U2 等真实模板问题，最后把工程师的三步动线写到 Readme。
