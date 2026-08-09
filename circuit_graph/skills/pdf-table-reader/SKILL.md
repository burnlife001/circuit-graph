---
name: pdf-table-reader
description: 从任意 PDF 中识别并提取所有表格(优化芯片 datasheet 的引脚/参数/电气特性表),跨页表格自动按同名表头合并,输出单一结构化 markdown 文件。触发词:PDF 表格, pdf 读表格, 提取 PDF 表格, pdf table extract, datasheet 表格, datasheet 提取, pin table, electrical characteristics, chip datasheet, pdf read table.
---

# pdf-table-reader

从任意 PDF 中识别并提取所有表格,默认针对芯片 datasheet 优化(引脚分配 / 电气参数 /
引脚复用等结构化表),但保留对任意带表格 PDF 的通用性。跨页表格按**同名表头**自动合并,
输出一份结构化 markdown,便于直接喂给 LLM 或人工查阅。

## 跑之前原封不动先念给用户

```plaintext
- 表格必须由真正的文字/线条组成;扫描件、纯图片、加密 PDF 不支持
- 用本机已有的 camelot 虚拟环境: E:\__work\BaseTools\camelot\.venv\Scripts\python.exe
  (camelot-py 2.0+ 基于 playa-pdf,无需 Java/Ghostscript;若用旧版 0.x 才需要)
- 提取结果仅供人/LLM 二次核对,关键数据请以原 PDF 为准
```

## 何时使用

- 拿到一份芯片 datasheet 或任意含表格的 PDF,想一次拿到所有表
- 想把电气参数/引脚分配/真值表等跨页大表合并成一张可读表
- 想以 markdown 形式批量归档 PDF 表格供后续检索

## 何时不使用

- PDF 是扫描件或图片 → 用 `firecrawl:firecrawl-parse` (OCR) 或 `douyin-video` 类工具先 OCR
- 表格内嵌在图片里无法文本选择 → 同样需先 OCR
- 只想看某一页的几行文字 → 直接用 PDF 阅读器更省事
- 已经用 `pdf-pin-extract` 处理过同一份 datasheet,本次只想拿引脚 → 用那个,不要重复提取

## 用法

```bash
# 用本机已有的 camelot 虚拟环境(Windows)
E:\__work\BaseTools\camelot\.venv\Scripts\python.exe \
  E:/yg/.claude/skills/__myskills/02.电子工具/pdf-table-reader/scripts/pdf_table_reader.py \
  path/to/DS_MM32F0140_EN.pdf

# 指定页码范围
... --pages 1-20
... --pages 3,5,7-12

# 指定输出文件(默认 tables-<pdf-stem>.md, 与 PDF 同目录)
... --out /path/to/out.md

# 强制 lattice 或 stream
... --flavor lattice
... --flavor stream
# 默认 auto: 先 lattice,0 表则降级 stream
```

Windows PowerShell 中文乱码时设 `$env:PYTHONIOENCODING="utf-8"`(不影响输出文件,文件恒为 UTF-8/LF)。

## 输出格式

`<pdf 文件名>.tables.md`,与 PDF 同目录(或 `--out` 指定路径)。结构:

```markdown
# DS_MM32F0140_EN.pdf 表格提取

> 提取时间: 2026-08-09 14:23 | 总表格数: 12 | 跨页合并: 2 张 | 引擎: camelot/lattice

---

## Table 1 · 第 3 页 · pin assignment (accuracy 95.2)

| Pin | Name | Type |
|---|---|---|
| 1   | VDD  | Power |
| 2   | PC13 | I/O  |
...

---

## Table 2 · 第 7-8 页 · electrical characteristics (跨页合并: 2 页)

| Symbol | Parameter | Min | Typ | Max | Unit |
|---|---|---|---|---|---|
| VDD   | Supply | 2.0 | 3.3 | 3.6 | V |
...
```

每张表的**首段表**(页/标题/accuracy)来自 camelot;**数据行**直接来自 `df.values`。
表头单元格含换行时用 `<br>` 保留;空表会被自动跳过。

## 跨页合并

datasheet 经常一页塞不下大表,下一页用相同表头继续。脚本逻辑:

1. camelot 逐页提取所有表 → 列表 `[(page, df), ...]`
2. 比较相邻页表格的**首行归一化后**是否完全一致(忽略空白、大小写、标点)
3. 一致 → 视为同一张表,合并(去掉第二页起的重复表头),输出标注 `(跨页合并: N 页)`
4. 不一致 → 独立输出

归一化只用于比较,输出保留原始文本。

## 表格标题猜测

camelot 不解析章节标题,所以表名来自**页内最近的非表文本**启发式:

- 优先看该页所有非表格文本中最接近表格顶部的一行(<=60 字符)
- 找常见章节关键词(`Table`, `Pin`, `Electrical`, `Characteristics`, `Register`, `Truth`, `AF`, `Multiplex`)
- 都没命中 → 标题写 `未命名(第 N 页)`

该启发式不保证准确,LLM 拿到输出后应核对表名。

## 硬依赖检查

脚本启动时检测依赖,任一缺失会**清晰报错并给出安装指引**,不静默失败:

| 依赖 | 检测方法 | 缺失时提示 |
|---|---|---|
| `camelot-py[base]` (>=2.0) | `import camelot; __version__` | `pip install -U "camelot-py[base]"` |
| `pdfplumber` (页面文本启发式) | `import pdfplumber` | `pip install pdfplumber` |
| `pypdf` (拿页数) | `from pypdf import PdfReader` | `pip install pypdf` |

**camelot-py 2.0+** 内部用 `playa-pdf` 后端,**不需 Java/gs**。脚本会读 `camelot.__version__`,
若主版本 < 2 才额外检测 Java/gs 并提示升级。

错误信息会**同时打印**缺失的依赖名称与对应安装命令,而不是只甩一个 ImportError。

## 局限

- 仅识别**文字型 PDF**(可文本选择的);扫描件/纯图片需先 OCR
- camelot 对**无线条、无明显边距空白**的纯文本流表格识别率低,默认 auto 模式会降级 stream
- 表格内有手写体/特殊符号(希腊字母、数学符号)→ 文本提取可能乱码
- 跨页合并只比较**首行归一化后是否一致**;**章节内连续多个同结构表**(如连续多个寄存器的
  Bit/Field/Description 表)可能误判为同一张表跨页,合并后行数暴增
  → 这种场景请用 `--pages` 缩小到单页范围,或事后人工拆分
- 表格标题来自启发式猜测(取页内最近章节关键词行),**仅供参考**,核对时以 PDF 章节标题为准
- 加密 PDF 需先解密
