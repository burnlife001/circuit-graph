---
name: keil-init
description: Initialize a Keil MDK project — analyze structure, generate build scripts, create CLAUDE.md with restricted zones, set up pre-commit hook. Triggers on: 初始化, 项目初始化, init Keil, keil-init, setup project, 建项目, 初始化工 Keil
---

# Keil MDK 项目初始化

> 通用版 — 适用于任何 Keil MDK 裸机/RTOS 固件项目（ARM Cortex-M/R, 51 等）。
> 整合 keil-batch-gen（编译脚本） + /init（CLAUDE.md） + pre-commit 钩子安装。

## 流程概览

```
1. 分析项目结构
2. 生成编译/烧录脚本（/keil-batch-gen）
3. 确定禁区与允许修改范围
4. 编写 .claude/CLAUDE.md
5. 创建 pre-commit 钩子 + settings.json
6. 安装到 .git/hooks/
7. 测试验证
8. 提交
```

---

## 执行步骤

### 第 1 步：分析项目结构

```bash
find . -maxdepth 2 -type f | head -100
find . -maxdepth 1 -type d | sort
find . -name "*.uvprojx" -o -name "*.uvproj"
find . -name "*.s" -o -name "*.S"
```

记录以下信息备用：
- **芯片型号** — `.uvprojx` → `<Device>` 标签
- **项目名** — `.uvprojx` 文件名（不含后缀）
- **OutputName** — `.uvprojx` → `<OutputName>` 标签
- **UV4.exe 路径** — 检查 `C:/Keil_v5/UV4/UV4.exe` 和 `D:/Keil_v5/UV4/UV4.exe`
- **工具链版本** — `.uvprojx` → `<pCCUsed>` 标签
- **供应商 HAL 目录** — 通常为 `Device/`、`Library/`、`Drivers/` 等
- **框架/RTOS 核心目录** — 若存在 RTOS/状态机框架（FreeRTOS、RT-Thread、QP/QP-nano、CMSIS-RTOS、uC/OS 等），其引擎/移植/配置文件属"逻辑禁区"，与供应商 HAL 同级禁止修改
- **各模块目录**功能

可选信息（**存在才记录，缺失则整段省略，不要硬造**）：
- **父级/共享规则文档** — 仓库或上级目录是否有可被 `@` 引用的共享规则文件（如 `../__shared_docs/*.md`），用于继承芯片级/团队级约束
- **原理图/硬件文档** — `__docs/`、`doc/`、`hardware/` 下的 `SCH_*.md`、引脚表等

### 第 2 步：生成编译/烧录脚本

通过 Skill 工具调用 `/keil-batch-gen`。**不要手动复制模板或替换占位符。**

```
Skill 参数: skill="keil-batch-gen", args=""
```

keil-batch-gen 会自动完成：
1. 查找 `.uvprojx`
2. 定位 `UV4.exe`
3. 读取 `<OutputName>`
4. 生成 `__build.ps1` / `__download.ps1` / `__build_and_download.ps1`

> UV4.exe 退出码不可靠，所有脚本通过解析日志文件判定结果。

### 第 3 步：确定禁区与允许修改范围

识别两类禁区与应用代码（可修改）。

#### 禁区 A — 供应商/工具链禁区（按 glob 模式匹配）

| 路径模式 | 理由 |
|----------|------|
| `Device/**` `Drivers/**` `Library/**` `Libraries/**` | 供应商 HAL 库 + CMSIS |
| `**/startup_*.s` `**/startup_*.S` | 启动文件，工具链自带 |
| `*.uvprojx` `*.uvoptx` `*.uvguix.*` | Keil IDE 工程配置 |
| `**/Objects/*` `**/Listings/*` | 编译产物（通常已在 .gitignore） |
| `RTOS/**` `Middleware/**` `ThirdParty/**` | 第三方库（视项目决定） |
| `*.scf` `*.sct` `*.ld` | 链接脚本（谨慎修改） |

#### 禁区 B — 逻辑/框架核心禁区（**仅当项目集成 RTOS/状态机框架时存在**）

框架引擎、移植层、调度核心等文件——非供应商 HAL，但同样禁止应用层修改。
逐文件列出（路径 + 作用），让 agent 知道"为什么"不能碰。无此类框架则**整段省略**。

| 文件 | 作用（示例，按实际填写） |
|------|------|
| `<框架>/<engine>.c` | 事件队列 / 调度器核心 |
| `<框架>/<port>.h` | 芯片移植层（临界区、WFI 等） |

#### 确定允许修改范围

将剩余目录归类为应用层代码，列出每个目录的职责。

> **规则**：让 agent 能一眼分清"能改"和"不能改"。禁区 A 用 glob 模式匹配（也写入 pre-commit 正则）；禁区 B 逐文件列出并说明作用。

### 第 4 步：编写 `.claude/CLAUDE.md`

> 如果仓库已有 `CLAUDE.md`，检查是否在根目录或 `.claude/` 中，更新而非覆盖。

CLAUDE.md 必须包含**免撞墙指令**，防止 agent 重复执行已完成的初始化工作。

模板内容如下（按需替换 `<占位符>`；标注"可选"的整段在不适用时删除）：

```markdown
# CLAUDE.md — <项目名>

> Firmware for **<芯片型号>** (Cortex-Mx, <Flash>KB / <RAM>KB).
> Built with Keil MDK v5 (ARMCC <版本>). <RTOS/裸机>.

<!-- 可选：有共享/父级规则时引用，继承芯片级约束 -->
> @`<相对路径>/shared_rules_xxx.md`，<芯片/团队>级约束继承自父级。

<!-- 可选：有硬件文档时指明 -->
## 原理图文件：`<__docs/SCH_xxx.md>`

## 🚫 禁区 A — 供应商/工具链（禁止修改）

| 路径 | 原因 |
|------|------|
| `<HAL目录>/**` | 供应商 HAL 库 + CMSIS |
| `**/startup_*.s` | 启动文件 |
| `*.uvprojx` `*.uvoptx` | Keil 工程配置 |
| `**/Objects/*` | 编译产物 |

<!-- 可选：集成 RTOS/状态机框架时才有 -->
## 🚫 禁区 B — 逻辑/框架核心（禁止修改）

| 文件 | 作用 |
|------|------|
| `<框架>/<engine>.c` | 事件队列 / 调度核心 |
| `<框架>/<port>.h` | 芯片移植层（临界区、WFI） |

## ✅ 允许修改范围

> 文件少时用一行清单；目录多时用表格。二选一。

仅限：`<file1>` `<file2>` `<file3>` …

| 目录 | 说明 |
|------|------|
| `<模块1>/` | 功能描述 |
| `<模块2>/` | 主程序入口 |

## 编译与烧录

```powershell
pwsh <uvprojx目录>/__build.ps1
pwsh <uvprojx目录>/__download.ps1
pwsh <uvprojx目录>/__build_and_download.ps1
```

## 架构

main loop / RTOS 任务调用关系图

## 功能模块

各协议/功能模块说明

## 关键定义

引脚映射、关键宏、调试配置等

## 记忆要点

RAM/Flash 限制、常见坑、注意事项
```

### 第 5 步：创建 pre-commit 钩子

#### 5.1 钩子脚本

创建 `.claude/hooks/pre-commit`：

```bash
#!/bin/bash
# pre-commit — 禁区执法钩子
# ⚠️ 此钩子已安装到 .git/hooks/pre-commit，agent 不要修改或绕过。
# 源文件: .claude/hooks/pre-commit
# 同步命令: cp .claude/hooks/pre-commit .git/hooks/pre-commit

set -euo pipefail

# 修改此处 RESTRICTED 正则以匹配项目的禁区路径
# 包含禁区 A（供应商/工具链）+ 禁区 B（框架核心文件，逐个加入，无则删去该段）
RESTRICTED='^(Device/|Drivers/|.*\.uvprojx$|.*\.uvoptx$|.*startup_.*\.s$)'
# 含框架核心示例：'^(Device/|Drivers/|.*\.uvprojx$|.*\.uvoptx$|.*startup_.*\.s$|src/qfn/|src/qvn/|.*qfn_port\.h$)'

STAGED_COUNT=$(git diff --cached --name-only --diff-filter=AM -z | wc -c)
if [ "$STAGED_COUNT" -eq 0 ]; then exit 0; fi

BLOCKED=$(git diff --cached --name-only --diff-filter=AM -z | grep -z -E "$RESTRICTED" | tr '\0' '\n' || true)

if [ -n "$BLOCKED" ]; then
  echo "========================================="
  echo "  BLOCKED — 以下文件在禁区内，禁止修改"
  echo "========================================="
  echo "$BLOCKED"
  echo ""
  echo "禁区包括: 供应商 HAL 目录 | .uvprojx/.uvoptx 工程配置 | startup_*.s 启动文件 | 框架核心文件"
  echo "如需修改禁区文件 → 创建 issue 由人工审批。禁止 agent 自行绕过。"
  exit 1
fi

exit 0
```

#### 5.2 settings.json

创建 `.claude/settings.json`：

```json
{
  "hooks": {
    "pre-commit": "bash .claude/hooks/pre-commit"
  },
  "permissions": {
    "allow": ["Bash","Read","Write","Edit","Grep","Glob","PowerShell","WebSearch","WebFetch"]
  }
}
```

### 第 6 步：安装到 git hooks

```bash
cp .claude/hooks/pre-commit .git/hooks/pre-commit
```

> 每次 `git clone` 后需执行此命令。钩子文件不跟踪在 `.git/` 中。

### 第 7 步：测试验证

```bash
# (1) 测试禁区拦截
echo "// test" >> <禁区目录>/xxx.h
git add <禁区目录>/xxx.h
git commit -m "should be blocked"
# 预期: BLOCKED，commit 被拒绝

# (2) 测试允许区通过
echo "// test" > src/test.c
git add src/test.c
git commit -m "should pass"
# 预期: 正常通过

# 清理
git reset --soft HEAD~1
git checkout -- .
rm -f src/test.c
```

### 第 8 步：提交到仓库

```bash
git add .claude/
git add <uvprojx目录>/__build.ps1 <uvprojx目录>/__build_and_download.ps1 <uvprojx目录>/__download.ps1
git commit -m "init: add CLAUDE.md, pre-commit hook, and batch build scripts"
```
