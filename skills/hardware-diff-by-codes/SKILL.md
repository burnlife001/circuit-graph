---
name: hardware-diff
description: Compare two MCU firmware project directories to determine if they target the same hardware PCB. Use this skill whenever the user asks whether hardware is the same, diff two boards, compare schematics from code, check PCB compatibility between firmware versions, or phrases like "is the hardware identical", "same board", "硬件是否相同", "电路板对比". Triggers on any hardware comparison request involving embedded firmware projects.
---

# MCU Firmware Hardware Diff

Determine whether two MCU firmware project directories target the **same physical PCB** or different hardware revisions.

## Principle

**Same PCB → same GPIO register final values (pin + mode + AF selection + ODR) across all peripheral init functions.**

A firmware difference in function logic, timing parameters, or optional driver code does NOT mean a hardware difference. Only pin assignment changes (different GPIO port/pin), direction changes (input↔output), AF number changes (different peripheral wired), or additional peripheral bus initialization indicate PCB changes.

## Standard Operating Procedure

Execute these steps in strict order. Do not skip. Do not classify prematurely.

---

### Step 0 — Determine MCU family and register model

Before any register analysis, identify the MCU family from project headers, startup files, or linker scripts (e.g. `stm32f1xx.h` → F1, `stm32f4xx.h` → F4, `mm32xxxx.h` → MM32).

- **F1 / MM32 (CRL/CRH model)**: use Steps 1–8 as written below.
- **F0/F4/L4/G4 (MODER/OTYPER/OSPEEDR/PUPDR model)**: same logical procedure, but decode `MODER[1:0]` (00=input, 01=output, 10=AF, 11=analog), `OTYPER` (0=PP, 1=OD), `OSPEEDR`, `PUPDR` (00=none, 01=pull-up, 10=pull-down), `AFR[0:1]` (4-bit per pin, AF0–AF15) instead of CRL/CRH nibbles. Apply this mapping in Step 4 — the decoding tables below are F1-specific; for F4-family use these MODER rules instead.
- **Other / unrecognized family**: flag "unsupported MCU family" and require manual analysis.

**If the two projects use different MCU families → different hardware. Stop here.**

### Step 1 — Confirm MCU and clock

Check both projects use the same MCU model and same system clock source/frequency.

- Scan `main()` for `SystemReInit()` or `SYSCLK_Init()` calls
- Check `RCC->` register writes
- **Different MCU = different hardware. Stop here.**

### Step 1.5 — Check for schematic / netlist (optional, execute if present)

If either project directory contains schematic documentation, netlist files, or pin-assignment tables, extract the **board-level net labels** for each MCU pin. These labels carry design intent that raw register values cannot convey.

- Look for files matching `SCH*`, `*.NET`, `*schematic*`, `*pinout*`, `*netlist*` in `__docs/`, `doc/`, `hardware/`, or project root.
- For each populated pin, record: **pin number | net name | on-board component**.
- If both projects have schematics, diff the net labels directly — a different net name on the same pin = different PCB.

**If only one project has a schematic**: use its net labels as ground truth for that project. For the other project, infer pin function from code alone, then flag mismatches between schematic labels and code usage as "needs manual review" in Step 7.

**If neither project has a schematic**: skip this step. The register comparison in Steps 2–7 remains the primary method.

### Step 2 — Collect all GPIO-affecting code

#### 2a. Quick pre-check: diff the pin-definition header

Before full simulation, diff the two projects' pin-macro definition file (commonly `GPIO/gpio.h`, `board.h`, `pin_mux.h`, `bsp_gpio.h`, or a similarly named header that defines pin-to-signal macros). This file typically contains:

- `#define LED1  GPIOB->ODR_F.P2` — pin-to-function output macros
- `#define SDA_OUT  GPIOD->ODR_F.P0` — bit-banged bus signal aliases
- `#define IPIDMI GPIOC->IDR_F.P14` — input signal aliases

**Macros present in only one project are early signals** — they flag pins worth closer scrutiny in Steps 3–5. Pins with identical macro definitions in both projects are candidates for the fast-path in Step 3.

#### 2b. Full collection: trace all GPIO register writes

From `main()` downward, collect every function that touches these registers across **all source files**:

| Register class | Example writes | What it controls |
|---------------|----------------|------------------|
| RCC AHBENR/APB1ENR/APB2ENR | `RCC->AHBENR \|= RCC_AHBENR_GPIOA` | Peripheral bus clock enable |
| GPIO CRL / CRH | `GPIOA->CRL &= ~0xF0; \|= 0x50` | Pin mode + direction |
| GPIO AFRL / AFRH | `GPIOA->AFRL \|= 0x02` | Alternate function number |
| GPIO ODR / ODR_F / BSRR / BRR | `GPIOA->BSRR \|= pin` | Output data / pull direction |
| GPIO IDR / IDR_F | `if (GPIOA->IDR_F.P0)` | Input reads (evidence of pin usage) |

**Collection scope:**
- Follow `#include` chains — init functions may live in different directories
- Include HAL library calls (`GPIO_Init()`) — decode their net effect on registers
- Include direct register writes in `.c` files, not just headers
- **`#ifdef` awareness**: when a GPIO/RCC write is guarded by `#ifdef X` / `#if defined(X)`, you MUST resolve which branch compiles:
  1. **Find the definition**: search for `#define X` in all headers reachable from the translation unit (the `.c` file, its `#include` chain, and any `-D` flags in build scripts or project config like `.uvprojx`, `Makefile`, `CMakeLists.txt`).
  2. **Evaluate the value**: `#define X 0` means `#if X` is false (the `#else` / `#endif` branch compiles). `#define X 1` or bare `#define X` means true. If the macro is NOT defined anywhere, `#if X` evaluates to false in C.
  3. **Simulate only the compiled branch** for each project. Do NOT simulate both branches and then try to reconcile.
  4. **If the macro evaluates differently between projects**, record this as a firmware configuration difference — the hardware may be the same but the feature set differs.

### Step 3 — Simulate final register state

**Critical: Compute the final value after all init code executes in order.** Do not compare individual `|=` statements — they accumulate.

For each GPIO register in each project, simulate from reset state:

```
Start state:
  CRL / CRH  → 0x4444_4444  (all pins = floating input)
  AFRL / AFRH → 0x0000_0000  (all AF = 0)
  ODR        → 0x0000_0000  (all bits = 0)

For each statement in execution order:
  reg |= val   →  final |= val
  reg &= ~val  →  final &= ~val
  reg = val    →  final = val   (overwrites all 16 pins)
  reg->ODR_F.Px = v  →  set/clear bit x in ODR final
  reg->BSRR |= pin   →  set corresponding ODR bit
  reg->BRR  |= pin   →  clear corresponding ODR bit
```

**Only compare final values between projects.** Intermediate step differences are irrelevant to hardware.

#### Fast-path: pins that are identical after `GPIO_init()`

In most projects, `GPIO_init()` (or equivalent) configures 80–100% of pins to their final state. Subsequent peripheral inits (`T16PWM_init`, `UART_init`, etc.) often reconfigure the **same** pins to the **same** CR/AF values already set by `GPIO_init()` — they are no-ops from a register perspective.

**Procedure**:
1. Simulate both projects through `GPIO_init()` only.
2. Mark pins with identical **(CR nibble, AF nibble)** at this point as **tentative SAME**.
3. For each subsequent init function, check only the **subset of pins that function touches** (by its `&= ~mask` / `|= mask` range). If it touches a tentatively-SAME pin, verify the final value still matches; if it touches a previously-different pin, update the comparison.
4. Any pin that survives all subsequent inits with unchanged CR/AF across both projects = **confirmed SAME**.

This avoids decoding 48 pins through 10+ init functions when 90% never change after `GPIO_init()`.

#### Filtering: `= 0` whole-register noise

Some projects start with `GPIOx->CRL = 0` (all pins → analog input) as defensive init. If:
1. The `= 0` is the **first** write to that register, AND
2. No subsequent `|=` / `&= ~` in any init function touches a given nibble, AND
3. No functional code reads/writes that pin (check IDR/ODR usage),

...then that nibble is **"unused"**. If the other project leaves the same nibble at reset (`0x4`), the difference is **ignored** — not a hardware diff.

#### Filtering: AF register only matters if pin is AF mode

Only apply AFRL/AFRH comparison to pins where the CRL/CRH nibble has CNF[3:2] = `1x` (AF mode). For non-AF pins, the AF register value is don't-care.

### Step 4 — Decode nibbles and AF numbers

#### 4a. CRL / CRH nibble → pin mapping

```
Register  [31:28] [27:24] [23:20] [19:16] [15:12] [11:8]  [7:4]  [3:0]
CRL         PA7     PA6     PA5     PA4     PA3     PA2     PA1    PA0
CRH        PA15    PA14    PA13    PA12    PA11    PA10     PA9    PA8
```
Replace PA with PB/PC/PD for other ports.

#### 4b. CR nibble → CNF + MODE decoding

```
Nibble = 0b CNF1 CNF0 MODE1 MODE0

MODE ([1:0]):
  00 = Input
  01 = Output 10MHz
  10 = Output 2MHz
  11 = Output 50MHz

CNF ([3:2]) — Input mode (MODE=00):
  00 = Analog
  01 = Floating
  10 = Pull-up / Pull-down  ← must check ODR bit
  11 = Reserved (invalid)

CNF ([3:2]) — Output/AF mode (MODE>00):
  00 = General Purpose Push-Pull
  01 = General Purpose Open-Drain
  10 = Alternate Function Push-Pull
  11 = Alternate Function Open-Drain
```

#### 4c. AFRL / AFRH nibble → pin mapping (same as CR)

```
AFRL  [31:28] [27:24] [23:20] [19:16] [15:12] [11:8]  [7:4]  [3:0]
        PA7     PA6     PA5     PA4     PA3     PA2     PA1    PA0
AFRH  [31:28] [27:24] [23:20] [19:16] [15:12] [11:8]  [7:4]  [3:0]
       PA15    PA14    PA13    PA12    PA11    PA10     PA9    PA8
```

AF nibble value = AF number (0–7 typically). A pin with CR nibble `0x9` (AF PP) + AF nibble `0x1` = UART1 TX; same CR nibble + AF nibble `0x2` = TIM1 CH1 → **different peripheral → different hardware**.

### Step 5 — Classify each pin difference

For each pin position (all ports, PA0..PD15), compare the final state tuple **(CR nibble, AF nibble, ODR bit)** between Project A and Project B.

```
Final state matches?
  ├── YES → SAME. Move to next pin.
  └── NO  → Classify:

  1. Is the nibble "unused" (reset/filtered =0 noise) in either project?
     └── YES → IGNORE. Not a real pin assignment.

  2. Is either nibble value Reserved (CNF=11, MODE=00)?
     └── YES → INVALID. Flag for manual review. Stop classifying this pin.

  3. MODE field: did direction change (Input=00 ↔ Output>00)?
     ├── YES → DIRECTION CHANGE.
     │         Pin function redefined (e.g. input key → output LED).
     │         Flag for MANUAL REVIEW. Do NOT auto-classify.
     └── NO  → Same direction. Continue.

  4. Both are input-mode (MODE=00):
     ├── Pull-up/down (CNF=10): compare ODR bit
     │   ├── ODR differs → MODE CHANGE, firmware (pull-up vs pull-down)
     │   └── ODR same → SAME
     ├── Analog→Floating, Floating→Analog → MODE CHANGE, firmware
     └── Analog→Pull-up, Floating→Pull-up → MODE CHANGE, firmware
         (all same-direction input-only changes)

  5. Both are output/AF-mode (MODE>00):
     ├── Pin number different → PIN CHANGE → hardware different
     ├── Pin same, MODE speed changed (10→50MHz) → MODE CHANGE, firmware
     ├── Pin same, CNF PP↔OD → MODE CHANGE, firmware
     ├── Pin same, GP↔AF → MODE CHANGE, firmware
     │   (e.g. PP out → AF PP = same pin, different function, not new hardware)
     ├── Pin same, CNF=AF PP in both, but AF nibble differs:
     │   └── AF NUMBER CHANGE → hardware different
     │       (trace connects the same pin to a different on-chip peripheral)
     └── Pin same, CNF=AF OD in both, but AF nibble differs:
         └── AF NUMBER CHANGE → hardware different
```

#### Decision summary table

| What differs | Classification | Reason |
|-------------|---------------|--------|
| Nothing | SAME | |
| Unused pin vs reset | IGNORE | Defensive `= 0` noise, pin never used |
| Reserved nibble 0x3/0x7/0xB | INVALID | Flag, do not classify |
| Different pin position | **HARDWARE DIFF** | PCB trace to different MCU pad |
| Different AF number (same AF-mode pin) | **HARDWARE DIFF** | Different on-chip peripheral wired |
| Direction In↔Out | **MANUAL REVIEW** | Function redefinition possible |
| Same pin, same direction, different CNF/MODE | Firmware | Drive tuning, speed, pull strategy |
| Same CNF=10 input, different ODR bit | Firmware | Pull-up vs pull-down choice |

### Step 6 — Check peripheral busses (RCC)

Compare RCC enable register final values:

```
RCC->AHBENR  — GPIO port clocks, DMA, etc.
RCC->APB1ENR — TIM2-7, I2C1-2, USART2-4, SPI2-3, etc.
RCC->APB2ENR — TIM1/14-17, ADC1, SPI1, USART1, etc.
```

- A bus bit enabled in only one project → lookup which peripheral it gates
- If that peripheral's GPIO pins are **also** only configured in that project → **hardware different**
- If pins are configured in both but bus only enabled in one → firmware didn't start the peripheral, same PCB

### Step 7 — Resolve peripheral ambiguities

For I2C/SPI device drivers that exist in one project but not the other:

| Evidence | Verdict |
|----------|---------|
| Bus pins configured as AF+I2C/SPI in both projects | Same PCB — chip pads exist, may or may not be soldered |
| Bus pins configured in one project, used for GPIO in the other | **Different PCB** — pads repurposed |
| Bus pins untouched (reset) in the other project | **Ambiguous** — could be unpopulated pads on same PCB |
| Bus enabled + chip init called in one, bus disabled in other | Firmware difference — chip soldering is the physical variable |

#### Bit-banged (software) I2C/SPI — special case

When I2C/SPI is implemented via GPIO bit-toggling (not hardware AF), the CR register may show plain GPIO output mode in **both** projects — hiding the functional difference. Detection:

- Look for `SDA_SETOUT()`, `SCL_OUT`, `SDA_OUT`, `SCL_IN`, `SDA_IN` macros or equivalent bit-bang patterns.
- Look for `start_IIC()` / `stop_IIC()` / `iic_write_reg()` that toggle ODR bits directly.
- Check whether a `#define ENABLE_I2C_PERIPHERAL 0` (or similar) forces the software path.

**If two pins have the same CR nibble in both projects but the pin-definition header (Step 2a) reveals one project binds them to I2C signal names while the other binds them to unrelated GPIO functions** → this is a **functional repurposing**. Classify as:

| Scenario | Classification |
|----------|---------------|
| Same CR, bit-bang I2C/SPI in A, unrelated GPIO in B | **AMBIGUOUS** — could be same PCB with different firmware allocation, or different PCB. Cross-reference Step 1.5 schematic if available. |
| Same CR, bit-bang I2C/SPI in A, unused (no references) in B | **AMBIGUOUS** — pins may be unpopulated chip pads on the same PCB |
| Same CR, bit-bang I2C/SPI in A, same bit-bang I2C/SPI in B | **SAME** — same communication bus, firmware details may differ |
| A uses hardware AF+I2C, B uses bit-bang GPIO on same pins | **Firmware** — peripheral choice, not PCB change (same pins wired to same I2C bus) |

#### Cross-reference with schematic (Step 1.5)

When Step 1.5 produced net labels, use them as tiebreakers:

- **Schematic net label = SDA/SCL → pin is I2C bus on PCB.** Bit-bang vs AF is firmware choice.
- **Schematic net label ≠ SDA/SCL (e.g. MOD_OUT) → pin is NOT I2C.** Different PCB.
- **No schematic for one project →** treat as ambiguous unless the functional usage is clearly incompatible (e.g., I2C requires open-drain + pull-up; driving a MOSFET gate requires push-pull — same pin can't serve both without a mux).

### Step 8 — Produce report

```markdown
## Hardware Comparison: [Project A] vs [Project B]

### Identity
- MCU: [model]
- Clock: [source] @ [freq]

### Pin-by-pin register comparison

| Port.Pin | CR A | CR B | AF A | AF B | ODR A | ODR B | Classification |
|----------|------|------|------|------|-------|-------|----------------|
| PA0      | 0x4  | 0x4  | 0x0  | 0x0  | 0     | 0     | SAME (unused)  |
| PA1      | 0x1  | 0x5  | 0x0  | 0x0  | 0     | 0     | MODE CHANGE (PP→OD) |
| ...      |      |      |      |      |       |       |                |

### Summary

**Hardware identical:**
- [count] pins — same pin, same mode, same AF, same ODR

**Hardware different:**
- [pin X]: PA3 in A vs PA4 in B — PCB trace moved
- [pin Y]: same PA8, AF1(UART1) in A vs AF2(TIM1) in B — different peripheral

**Firmware only (same PCB):**
- [count] pins — same pin, mode change only (PP→OD, speed, pull direction)

**Functional repurposing (same CR, different usage — see Step 7):**
- [pin M]: same CR=0x1 in both, but A uses as bit-bang I2C SDA, B uses as [function] — ambiguous without schematic

**Requires manual review:**
- [pin K]: direction change input→output
- [pin N]: invalid nibble 0xF

### Verdict
- [ ] Same PCB, different firmware
- [ ] Same PCB, optional chip not soldered in one variant
- [ ] Different PCB revision
- [ ] Requires manual review (schematic needed for ambiguous pins)
```

## Key rules

1. **Simulate final register state; compare finals, not intermediates.** `|=` accumulates. A sequence of 3 writes to PA1 converges to one final nibble — that's what you compare.
2. **Never trust comments. Hex value is sole truth.** `// PA.4+PA.6` next to `|= 0x50` is wrong. Decode: `0x50` nibble [7:4] = PA1. Always.
3. **Same nibble position + same direction + mode change = firmware. Different nibble position = hardware. AF number change on AF pin = hardware.** Direction change (In↔Out) = manual review.
4. **`= 0` defensive init is noise.** Filter it: if no later code touches that pin, it's "unused", not a hardware difference.
5. **Track ODR through BSRR/BRR/ODR_F, not just direct ODR writes.**
6. **AF nibble matters only when CNF = AF mode.** For GP pins, ignore AF value.
7. **External chip driver ≠ chip soldered.** Same PCB may have unpopulated pads.
8. **When uncertain, classify as "ambiguous" or "manual review".** Do not force a conclusion.
9. **`#ifdef` guards determine what compiles.** See Step 2b — grep the actual `#define` value, resolve the truth value for each project, and simulate only the compiled branch.
10. **Same CR nibble ≠ same function.** Bit-banged I2C/SPI uses GPIO output mode (same CR as any other GPIO output). Cross-check pin-definition headers (Step 2a) and schematic net labels (Step 1.5) to detect functional repurposing. Two pins with identical CR values but bound to incompatible signal names (e.g. `SDA_OUT` vs `MOD_OUT`) are functionally different — defer to Step 7 classification.
11. **Schematic net labels override code inference.** When Step 1.5 is available, board-level net names are ground truth for PCB intent. Code may configure a pin in a mode that doesn't match its PCB connection (e.g., output mode on a pin wired only to an ADC input — the PCB still has the ADC circuit).
