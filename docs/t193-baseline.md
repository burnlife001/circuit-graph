# T193 板卡基线（P5 建立）

建立：2026-11-01（模板共建计划 P5）  
网表：`fixtures/protel/T193.NET`（54 元件）  
基准：`fixtures/protel/T193.answers.jsonl`（54 行，全部 `source=cg-role` 种子）

## 基线数字

- 识别器命中：21/54（7 个模板），fallback 33
- 对齐率：54/54（100%）——**注意这是循环基线**（种子即当前输出），
  真实对齐率要等工程师纠正后才算数
- FX-T268 回归：全量 pytest 通过（见提交记录）

## 种子说明

全部 54 行经 `nl_to_jsonl.py` 落库（dry-run 先行、`.bak` 备份），
`source=cg-role` 为最低权威——工程师一句自然语言即可按
`human > session-correction > cg-role` 升级任何一行。
fallback 行 block 如实记 `(unmatched)`。

## 待工程师确认的优先候选（按可疑/价值排序）

| 位号 | 当前种子定性 | 为什么需要人看一眼 |
|---|---|---|
| Y1 / Y2 | NPN 低边开关 | 位号 Y 通常是晶振；22P 电容 C12/C13 恰是晶振负载电容的典型值——疑似 NPN 模板误命中 |
| IC2 | NPN 低边开关 | IC 位号被判成 NPN 开关，可能是封装成 IC 的驱动/三极管，需确认 |
| LCD1 | 电感 LCD_4COM-12SEG | 4COM-12SEG 是段码 LCD 模组，不是电感；网表类型字段疑似有误 |
| R11 | 电流采样电阻 | 温度计板上出现电流采样较反常（MAX6675 热电偶不需要），确认是否真的采样 |
| D1 | 二极管 1N4148 | 只有型号没有功能定性，确认在板作用（保护/整流/隔离） |
| C12 / C13 | Capacitor 22P | 若 Y1 是晶振，这俩大概率是负载电容 → 可补 crystal-with-load-caps 模板正例 |
| F1 | Unknown S1206-0.5A | 0.5A 贴片大概率是保险丝/熔断器，确认后可入 KB |
| R3 / R5 / R7 | Resistor 10K | 未命中任何模板，确认功能后可反推模板缺口 |
| Couple1 | Capacitor Couple | 耦合电容未定性，确认其耦合路径 |

## 模板反馈信号（暂不动作，待确认后）

- 晶振+负载电容拓扑（Y1+C12+C13 若确认）→ `crystal-with-load-caps` 已有模板，
  可能是该板接法不标准导致未命中，值得做正例反查
- HT7333 / HT1621B / MAX6675 / PCF8563 / AT45DB321D 均为 KB 未知型号 →
  后续 datasheet 录入 KB（`scripts/import_pin_json.py` 流程），不靠猜
