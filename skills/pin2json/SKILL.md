---
name: pin2json
description: 输入元件型号提取引脚信息,输出JSON。触发词: pin2json, 元件入库.
---

## JLCPCB 元件搜索,输出引脚定义JSON

- 直接传入型号(如`LM321`),自动搜索 JLCPCB 并取第一个结果
- `-p`/`--package` 必选参数(如`-p SOT-23-5`)
- 也支持直接传 C-id(如`C9405`)或 `.kicad_sym` 文件路径

## 输出格式(纯 JSON,无 Markdown 包裹)

```json
{
  "symbol": "LM321MFX",
  "package": "SOT-23-5",
  "source": "C9405",
  "pin_count": 5,
  "pins": {
    "1": "+",
    "2": "GND",
    "3": "-",
    "4": "OUT",
    "5": "V+"
  }
}
```
