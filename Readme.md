# circuit-graph

**一行命令说清楚电路用途。**

读取已有电路网表，解释每个元件和功能块在这块板子上的作用：先给结论，再列连接、引脚和拓扑证据。认不出的部分明确说明，不编造答案。

```powershell
cg role R100 board.NET
cg role --all board.NET
```

第一条解释一个元件，第二条一次解析、逐个解释整板元件。整板输出是各部分作用的汇总，不是自动猜测整机产品用途。

## 能做什么

| 问题 | 命令 |
|---|---|
| 这个元件在电路中有什么作用？ | `cg role R100 board.NET` |
| 整板各个元件分别做什么？ | `cg role --all board.NET` |
| 它连到哪里，证据是什么？ | `cg query board.NET --conn R100` |
| 周围有哪些功能块、供电和通信关系？ | `cg view R100 board.NET --with-all` |
| 这颗芯片的引脚是什么功能？ | `cg kb lookup LM321` |
| 哪些元件已被拓扑模板覆盖？ | `cg primitives coverage board.NET` |

识别依据来自网表连接、元件参数、拓扑模板和本机引脚知识库。支持运放反馈、分压、上下拉、滤波、去耦、电流采样、电源转换、通信接口、时钟和复位等常见功能；覆盖范围随模板积累扩展。

**范围只有电路理解**：不生成或设计电路，不做 SPICE 仿真、自动布局或电气/布局规则检查。图导出和知识库维护只服务于理解与核验。

## 安装

在项目目录打开 PowerShell：

```powershell
powershell -ExecutionPolicy Bypass -File ./install.ps1
```

重新打开终端后检查：

```powershell
cg --version
cg --help
```

当前版本为 `0.2.0`。脚本支持 `-Dev`（开发依赖）、`-SkipPath`（不改 PATH）、`-NoPause`（不等待回车）。未配置 PATH 时可用 `./.venv/Scripts/cg.exe`。

## 用自己的板子

从 Altium Designer / Protel 导出 Protel 格式 `.NET` 网表，然后直接查询：

```powershell
cg role R100 board.NET
cg role --all board.NET --format json
```

主分析链支持 Protel 格式网表（扩展名 `.NET` / `.net`）。KiCad 导出的网表可通过 `cg kb import kicad` 导入元件知识库；原生 `.kicad_sch`、`.SchDoc` 原理图不作为主分析链输入。

### 引脚知识缺失时

`cg role` 默认离线，只读本机知识库。缺失引脚时降低结论的确定性，保留实际连接依据。需要补全时显式选择来源：

```powershell
cg role IC7 board.NET --kb-source datasheet
cg role IC7 board.NET --kb-source pin2json
cg role --all board.NET --kb-source pin2json
```

`datasheet` 使用本地数据手册；`pin2json` 联网查询元件库。补全结果报告成功、失败和跳过，不伪造引脚。

## 纠正识别结果

把正确作用连同证据记录下来，维护这块板的答案库；若需要扩展通用模板，应同时添加反例约束并运行回归测试。

详见[模板共建资产说明](docs/template-system-assets.md)。CLI 自身不会把一句自然语言自动学习成新规则。

也可以在浏览器中可视化编辑（知识库与模板层同一个入口）：

```powershell
./__webui.ps1
# 或
./.venv/Scripts/python.exe scripts/webui_server.py
```

打开 http://127.0.0.1:8766/ 是入口页，进去后有两个页面：

- `/kb` —— 知识库编辑器：查元件、引脚，录入新的元件与引脚。
- `/tpl` —— 模板层工作台：查看模板、在板子上回放匹配、按芯片引脚生成模板草稿。它只写模板目录（`primitives/`），默认只读，勾选允许写入后才会保存。
  - 其中「datasheet 对比」页签：左边是芯片 datasheet 的应用电路原图，右边是网表转换出的引脚拓扑；点击右侧引脚，左侧 PDF 上会高亮对应文字，便于核对封装引脚定义。

## 接入其他项目

在目标项目目录执行：

```powershell
cg init
```

它检测网表、写入 `.cg.toml` 并初始化知识库，不修改目标项目的 Claude 配置。可用 `--dry-run` 预览，`--skip-config`、`--skip-kb` 跳过对应步骤。

## 开发与参考

```powershell
powershell -ExecutionPolicy Bypass -File ./install.ps1 -Dev -NoPause
./.venv/Scripts/python.exe -m pytest -q
```

- [CLI 参考](docs/cli-reference.md)
- [架构说明](docs/architecture.md)
