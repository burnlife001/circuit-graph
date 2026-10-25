---
name: armgcc-build-download
description: Build and flash MM32G0001 firmware for QP-nano or lptask LED blinky projects. Use whenever the user wants to compile, flash, or verify firmware on MM32G0001 chip. Triggers on phrases like "build", "flash", "烧录", "编译", "验证 LED", "download firmware"
---

# ARM-GCC Build & Download Skill

Build and flash bare-metal Cortex-M (MM32/ARM) firmware.
**Project-agnostic**: the scripts here know nothing about any single project —
copy the skill folder into another project and it works as-is.

```
armgcc-build-download/
├── SKILL.md
├── chips.json          # 芯片表：G0001 -> target / pack / mcpu / startup / linker script
└── scripts/
    ├── build.py        # 通用 arm-gcc 编译（配置驱动 + 自动发现）
    └── flash.py        # 通用 pyOCD 烧录（flash + reset; continue）
```

## Hardware Context（默认芯片 G0001）

| Item | Value |
|------|-------|
| Chip | MM32G0001A1TC (Cortex-M0+) — `--chip G0001` |
| Board | G0001 (mini)，见 `__docs/SCH_Mini-G0001.md` |
| Flash | 16KB @ 0x08000000 |
| SRAM | 2KB @ 0x20000000 |
| Pack | MindMotion/MM32G0001_DFP/1.0.1 |

芯片表在 `chips.json`，新增芯片只改这个文件（`build.py` / `flash.py` 共用）：
```json
{ "G0001": { "target": "MM32G0001A1TC",
              "pack": "D:/Keil_v5/Packs/MindMotion/MM32G0001_DFP/1.0.1",
              "mcpu": "cortex-m0plus",
              "startup": "startup_mm32g0001_gcc.S",
              "linker_script": "MM32G0001.ld" } }
```

## Toolchain 查找顺序（`build.py` 自动探测，无需配置）

1. 配置里的 `toolchain`
2. 环境变量 `GCC_PATH`
3. `E:/__electric/tools/arm-gcc/*`（也兼容 `/e/__electric/...` 的 MSYS 路径）
4. `PATH` 里的 `arm-none-eabi-gcc`

启动文件 / 链接脚本查找顺序：配置 → 芯片表 → 工具链旁的 `gccPrjFiles/` → 项目目录。

⚠️ 旧路径 `E:/__electric/MM32/tools/...` 与 `E:/__electric/MM32/frameResearch/fw-repo-tmp` 已废弃，勿用。

## Build — 通用动作

```bash
python <skill>/scripts/build.py                      # 编译当前目录的项目
python <skill>/scripts/build.py --project ../other   # 编译别的项目
python <skill>/scripts/build.py --chip G0001 --flash # 编译后直接烧录
```

**项目配置**：`armgcc.build.json` 放在项目根目录（可选）。
没有配置文件时自动发现：`src/**/*.c` + `Device/*/Source/*.c` + `Device/*/HAL_Lib/src/*.c`，
include 自动取 `src/`、含 `.h` 的 `src/*/`、`src/*/include`、`Device/*/Include` 等。

配置字段（全部可选，缺省走自动发现）：

| 字段 | 说明 |
|------|------|
| `target` | 输出文件名（默认项目目录名） |
| `build_dir` | 输出目录（默认 `build`） |
| `sources` | 显式源文件列表（相对项目根） |
| `source_globs` / `exclude` | 自动发现时的 glob 与排除规则 |
| `includes` | 显式 include 列表，**顺序有意义** |
| `startup` / `linker_script` | 启动文件 / 链接脚本 |
| `toolchain` | arm-gcc 安装目录 |
| `cflags` / `ldflags` / `asflags` | 覆盖默认编译/链接/汇编选项 |
| `mcpu` | 覆盖芯片表里的 mcpu |

自动发现的排除项（框架内部，别编进去）：
- `*/ports/arm-cm/*` — Keil 专用移植层，与项目自带 `src/ports` 符号冲突
- `*/qstamp.c` — 内嵌 `__DATE__/__TIME__`，破坏可复现构建

产出 `build/<target>.{elf,hex,bin,map}`，并打印链接器给出的 FLASH/RAM 占用。

**本项目（04_QPnano_LED_Key_ModeSwitch）**：`armgcc.build.json` 显式列出 8 个源文件，
与原 `__build_gcc.sh` 产物 **byte-identical**（hex md5 一致：`5227d14d…`）。
`__build_gcc.sh` 保留作参考/回退。

项目特有注意事项：
- 移植层用项目自带 `src/ports/`，include 顺序 `-Isrc/ports` 须在 `-Isrc/qpn/include` 之前
- 无 HAL 依赖（bsp 直接操作寄存器，只 include `mm32g0001.h`）

## Flash — 规则

**默认走本技能的 Python 脚本**（`scripts/flash.py`，默认芯片 = G0001 / MM32G0001A1TC），
脚本内部完成「pyocd flash → reset; continue」两步，pyocd 不在 PATH 也能跑（自动找解释器或 `pyocd.exe`）。

### 方式 1：技能脚本（推荐，烧 GCC 产物）

```bash
python <skill>/scripts/flash.py
# 指定镜像 / 换芯片 / 换 pack：
python <skill>/scripts/flash.py build/QPnano_LED_Key_ModeSwitch.hex
python <skill>/scripts/flash.py --chip G0001 --target MM32G0001A1TC --pack D:/path/to/DFP/1.0.1 fw.hex
```

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `image` | 自动取 `build/` 或 `MDK-ARM/Objects/` 中最新的 .hex/.bin/.elf | 待烧镜像 |
| `--chip` | `G0001` | 取 `chips.json` 里的 target / pack |
| `--target` | 芯片表里的值 | pyOCD target type |
| `--pack` | 芯片表里的值 | CMSIS pack 目录 |
| `--frequency` | `1000000` | SWD 时钟 Hz；No ACK 时降到 100000 |
| `--probe` | 第一个找到的探针 | 探针 unique id |
| `--no-reset` | 关 | 跳过 `reset; continue`（芯片会停在 halted） |

### 方式 2：MDK 脚本（烧 MDK 产物）

```bash
pwsh -NoProfile -File MDK-ARM/__download.ps1
```

注意：烧录的是 MDK 产物（`MDK-ARM/Objects/` 下的 axf/hex），**不是** GCC 的 `build/*.hex`。MDK 工程需先编译过才有产物。

### 故障切换

- `flash.py` 报错 → 🚫禁止修改本脚本，stop 并上报
- `__download.ps1` 报错 → 🚫禁止修改脚本，改用 `flash.py` 或 stop 上报
- 两者都失败 → stop 并上报，不要反复重试
- SWD 报 `No ACK` / `Target not detected` → 99% 是**硬件没接好**（探针 SWD 排线没插到底、VTref 未接、目标未供电），
  不是软件问题；先查 `powershell -NoProfile -Command "Get-PnpDevice -PresentOnly -Class Ports"` 确认板子是否已被系统枚举。

## Verification Steps

After flash + resume, verify by LED behavior:

**QP-nano — 3 modes, cycle by PA2 key press:**
| Mode | Behavior |
|------|----------|
| 0 | Breathing (PWM ~10ms resolution, 1s cycle) |
| 1 | Fast Blink (200ms on / 200ms off) |
| 2 | Slow Blink (500ms on / 500ms off) |

串口验证：用 `serial-monitor` 技能抓 UART1 (PA10) 输出；按键可用串口发送 `k1,k2,k3,k4` 模拟（见 shared_rules_all.md）。

## Known Issues

1. **pyOCD v0.44.x halt after flash**: Must run `reset; continue` commander command after flash, otherwise chip is frozen.
2. **SP value**: 链接脚本 `_estack = 0x20000000 + 2K`（2KB SRAM 顶）。SP 错误会导致 push/pop 静默失败。
3. **Without SystemInit()**: Clock runs at HSI/6 = 8MHz. LED timing calculations must use 8MHz, not 48MHz.
4. **厂商文件警告**: `Device/MM32G0001/Source/system_mm32g0001.c` 有 2 个 unused-variable 警告，属正常，不要修改 Device/ 禁区文件去消除。
