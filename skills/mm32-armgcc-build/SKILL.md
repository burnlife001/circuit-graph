---
name: mm32-armgcc-build
description: Build and flash MM32G0001 firmware for QP-nano or lptask LED blinky projects. Use whenever the user wants to compile, flash, or verify firmware on MM32G0001 chip. Triggers on phrases like "build", "flash", "烧录", "编译", "验证 LED", "download firmware"
---

# MM32 Build Skill

Build, flash, and verify MM32G0001 firmware.

## Hardware Context

| Item | Value |
|------|-------|
| Chip | MM32G0001A1TC (Cortex-M0) |
| Flash | 16KB @ 0x08000000 |
| SRAM | 2KB @ 0x20000000 |
| LED | PA11 (active LOW) |
| KEY | PA2 (floating, active LOW) |
| UART1 | PA10 TX, 115200 n8.1 |
| Pack | MindMotion/MM32G0001_DFP/1.0.1 |

## Build Commands

### QP-nano (qpn_test)
```bash
cd qpn_test
ROOT=/e/__electric/MM32/frameResearch/fw-repo-tmp
DEVICE=E:/__electric/MM32/coding/03_UART1_Debug_LED_Mode_Switch/Device
QPN=$ROOT/qpn_test/qpn
CC=/e/__electric/MM32/tools/xpack-arm-none-eabi-gcc-15.2.1-1.1/bin/arm-none-eabi-gcc
OBJCOPY=/e/__electric/MM32/tools/xpack-arm-none-eabi-gcc-15.2.1-1.1/bin/arm-none-eabi-objcopy
SIZE=/e/__electric/MM32/tools/xpack-arm-none-eabi-gcc-15.2.1-1.1/bin/arm-none-eabi-size

CFLAGS="-mcpu=cortex-m0 -mthumb -Os -ffunction-sections -fdata-sections -Wall -Wextra -Wno-unused-parameter -I${QPN}/include -I${QPN}/ports -Isrc -I${DEVICE}/MM32G0001/Include -I${DEVICE}/CMSIS/Core/Include -DNDEBUG --specs=nosys.specs --specs=nano.specs"
LDFLAGS="-mcpu=cortex-m0 -mthumb -T${ROOT}/common/linker_script.ld -Wl,--gc-sections -nostartfiles --specs=nosys.specs --specs=nano.specs"

$CC $CFLAGS -c $ROOT/common/startup.c -o startup.o
$CC $CFLAGS -c src/main.c -o main.o
$CC $CFLAGS -c src/blinky.c -o blinky.o
$CC $CFLAGS -c src/bsp.c -o bsp.o
$CC $CFLAGS -c ${DEVICE}/MM32G0001/Source/system_mm32g0001.c -o system.o
$CC $CFLAGS -c ${QPN}/src/qfn.c -o qfn.o
$CC $CFLAGS -c ${QPN}/src/qepn.c -o qepn.o
$CC $CFLAGS -c ${QPN}/src/qvn.c -o qvn.o
$CC $CFLAGS -c ${QPN}/ports/qvn_port.c -o qvn_port.o
$CC $LDFLAGS main.o blinky.o bsp.o startup.o system.o qfn.o qepn.o qvn.o qvn_port.o -o qpn_test.elf
$OBJCOPY -O ihex qpn_test.elf qpn_test.hex
$OBJCOPY -O binary qpn_test.elf qpn_test.bin
$SIZE -A -x qpn_test.elf
```

Move artifacts to `output/` after build:
```bash
mkdir -p output
mv *.o *.elf *.hex *.bin *.map output/
```

### lptask_test
```bash
cd lptask_test
ROOT=/e/__electric/MM32/frameResearch/fw-repo-tmp
DEVICE=E:/__electric/MM32/coding/03_UART1_Debug_LED_Mode_Switch/Device
LPTASK=$ROOT/lptask_test/lptask
CC=/e/__electric/MM32/tools/xpack-arm-none-eabi-gcc-15.2.1-1.1/bin/arm-none-eabi-gcc
OBJCOPY=/e/__electric/MM32/tools/xpack-arm-none-eabi-gcc-15.2.1-1.1/bin/arm-none-eabi-objcopy
SIZE=/e/__electric/MM32/tools/xpack-arm-none-eabi-gcc-15.2.1-1.1/bin/arm-none-eabi-size

CFLAGS="-mcpu=cortex-m0 -mthumb -Os -ffunction-sections -fdata-sections -Wall -Wextra -I${LPTASK}/src -I${DEVICE}/MM32G0001/Include -I${DEVICE}/CMSIS/Core/Include -DNDEBUG --specs=nosys.specs --specs=nano.specs"
LDFLAGS="-mcpu=cortex-m0 -mthumb -T${ROOT}/common/linker_script.ld -Wl,--gc-sections -nostartfiles --specs=nosys.specs --specs=nano.specs"

$CC $CFLAGS -c $ROOT/common/startup.c -o startup.o
$CC $CFLAGS -c src/main.c -o main.o
$CC $CFLAGS -c src/port.c -o port.o
$CC $CFLAGS -c ${LPTASK}/src/scheduler.c -o scheduler.o
$CC $LDFLAGS main.o startup.o port.o scheduler.o -o lptask_test.elf
$OBJCOPY -O ihex lptask_test.elf lptask_test.hex
$OBJCOPY -O binary lptask_test.elf lptask_test.bin
$SIZE -A -x lptask_test.elf
```

## Flash Commands

```bash
# Flash firmware.hex
pyocd flash --pack "D:/Keil_v5/Packs/MindMotion/MM32G0001_DFP/1.0.1" -t MM32G0001A1TC -e chip firmware.hex

# Resume after flash (pyOCD v0.44.x quirk — chip stays halted after flash/reset)
pyocd commander --pack "D:/Keil_v5/Packs/MindMotion/MM32G0001_DFP/1.0.1" -t MM32G0001A1TC -c "reset; continue"
```

## Verification Steps

After flash + resume, verify by LED behavior:

**QP-nano (qpn_test) — 3 modes, cycle by PA2 key press:**
| Mode | Behavior |
|------|----------|
| 0 | Breathing (PWM ~10ms resolution, 1s cycle) |
| 1 | Fast Blink (200ms on / 200ms off) |
| 2 | Slow Blink (500ms on / 500ms off) |

Startup sends "hello world\r\n" on UART1 (PA10).

**lptask_test:**
- LED blink at task intervals (100ms / 500ms)

## Known Issues

1. **pyOCD v0.44.x halt after flash**: Must run `reset; continue` commander command after flash, otherwise chip is frozen.
2. **SP value**: `startup.c` must set SP to `0x20000800` (2KB SRAM top). Wrong SP causes all push/pop to fail silently.
3. **Without SystemInit()**: Clock runs at HSI/6 = 8MHz. LED timing calculations must use 8MHz, not 48MHz.

## Build Artifacts Location

Always check `output/` directory after build:
- `*.o` — Object files
- `*.elf` — ELF executable
- `*.hex` — Intel HEX for flashing
- `*.bin` — Binary image
- `*.map` — Linker map
