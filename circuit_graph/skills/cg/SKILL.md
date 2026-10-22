---
name: cg
description: Use when analyzing electronic circuits — Protel .NET netlists, KiCad .kicad_sch, ERC checks, component knowledge base, power/communication context, or chip pinouts. Triggers on "R100 是什么", "C9 的作用", "这个元件干嘛用的", "为什么要有这个电阻", "电路分析", "netlist", ".NET file", "kicad_sch", "ERC", "稳压器链", "I2C 总线", "chip pinout", "元件数据库".
---

# cg (circuit-graph analysis)

Use the `cg` CLI to analyze circuits instead of hand-parsing netlists. The CLI preserves 100% of the original IR — no information loss, no re-implementation of parsing.

## 0. The CLI is global — call `cg` directly

`cg` is installed globally (`C:\Users\yg\.local\bin\cg.exe`, v0.1.0). No PATH probe, no venv path, no `& $CG` indirection.

```powershell
cg --version          # ~200 ms
```

If a session ever reports `cg` not found, the global install was lost — reinstall it. Do **not** fall back to the repo venv copy; it is stale.

## 1. Fast path (default): `cg query`

One command per question. Measured **200–250 ms** each (IR parse is cached), far inside the 2 s budget.

```powershell
$NET = "<project>\__docs\FX-T268-V6-10米-5065.NET"
cg query $NET --conn C9                      # pins, nets, co-members (with values)
cg query $NET --net SW2                      # net roster
cg query $NET --comp C9                      # one designator: pins -> nets
cg query $NET --find SW5001                  # fuzzy search designator/value/part
cg query $NET --all --type mosfet            # list by device kind
cg query $NET --conn C9 --format json        # structured output for scripts
```

- `--comp` takes **one** value. Repeat the flag for more: `--comp C9 --comp R26`. Passing several words after one flag is a usage error.
- `--conn` also lists every co-member on each pin net — that is usually the only call needed to answer "what is X for".
- `--cache-dir PATH` / `--no-cache` control the IR cache (default `<repo>/.cg-cache`).
- The legacy `scripts/cgq.py` helper (bundled next to this file) is **no longer needed**: `cg query` covers the same questions natively and is faster to type. Only use it if you need its multi-flag convenience in a single shell pipeline.

## 2. The 2-second budget (hard rule)

Every answer must land **under 2 s per command**. If a command is slower, the plan is wrong — re-plan instead of waiting:

| Do | Don't |
|----|-------|
| `cg query --conn/--net/--comp/--find` (one question each) | `cg view --hops 3` (floods output, still parses the whole sheet) |
| `cg kb lookup <part>` for pin names | regex-grepping the raw `.NET` (no designer comments, and the skill forbids it) |
| `cg query --format json` when a script consumes the result | one `cg view` per question (re-parses every time) |

Batching: a single PowerShell call may issue several `cg query` commands in sequence (each ~200 ms) — that is the intended way to explore a sub-circuit.

## 3. `cg view` — still the right tool for structure

```powershell
cg view R100 <file.NET> --pretty          # 2-hop ego subgraph (default)
cg view U2  <file.NET> --with-power       # power chain / upstream regulator
cg view U2  <file.NET> --with-comm        # I2C / UART / SPI / PWM buses
cg view U2  <file.NET> --with-all         # both + functional blocks
```

Use it when you need the *subgraph* or the power/comm context layer, not for listing parts. `--hops` > 2 floods the output with unrelated parts — don't. `cg view` prints warnings to stderr; they are not errors.

## 4. Knowledge Base

```powershell
cg kb lookup <part_number>   # details + pin map + pin_source (IC pin NAMES live here)
cg kb search <keyword>       # full-text
cg kb pinout <part_number>   # pin map only
cg kb import protel <file.NET>   # ingest netlist, auto-resolves IC pins
cg kb import kicad <file.net>   # KiCad netlist ingestion
cg kb stats
```

KB lookup is the way to turn a net into a pin function (`SW2` → `SW5001 pin15 = SW2`).

## 5. ERC (KiCad `.kicad_sch` ONLY)

```powershell
cg erc check <file.kicad_sch>                       # native engine (4 validators)
cg erc check <file.kicad_sch> --engine kicad        # kicad-cli wrapper
cg erc check ... --format text --pretty             # human readable
cg erc check ... --severity error                   # filter
```

Exit codes: `1` if any ERROR, `0` otherwise — CI-friendly.

## 6. Setup in a fresh project (`cg init`)

```powershell
cg init                       # writes .cg.toml + KB init + installs bundled skills
cg init . --yes               # skip interactive confirm (CI / scripts)
cg init . --force             # overwrite existing .cg.toml + re-run sub-steps
cg init . --dry-run           # print the plan, no writes
cg init . --skip-skill        # only config + KB
```

`cg initskill <path>` installs every bundled skill under `<target>/.claude/skills/<name>/` and copies the bundle `README.md` routing table to `<target>/.claude/skills/README.md`. Re-runs are skipped without `--force`.

## 7. Performance rules (measured on T268-V6, 2026-10-22, global `cg` v0.1.0)

| Operation | Latency | Verdict |
|-----------|---------|---------|
| `cg query --conn/--net/--comp/--find` | 200–250 ms | **most effective** — one command per question, cached IR |
| `cg kb lookup <part>` | ~215 ms | effective — the only way to get IC pin names |
| `cg view X --pretty` (2 hops) | ~250 ms | effective but verbose — keep to 1–2 calls, for structure |
| `cg view X --hops 3` | — | ineffective — floods output with unrelated parts |
| regex-grepping the raw `.NET` | — | ineffective — no designer comments; use the graph |
| hand-parsing the `.NET` / `.SchDoc` | — | **forbidden** — cg owns netlist parsing (see Red Flags) |

## 8. Red Flags — Stop and Re-Route

- **Opening the .NET in a text editor or writing a parser for it.** Use `cg query --conn/--net/--comp/--find`; the raw file is slow, error-prone, and drops the graph context. (A `.SchDoc` is *not* a netlist and cg cannot read it — see §9 for the one sanctioned exception.)
- **Running `cg erc` on a Protel `.NET` file** — unsupported format, exits 2 with `unsupported schematic format: '.NET'`. Use `cg erc` only on `.kicad_sch`; for Protel, ingest via `cg kb import protel`.
- **Treating `cg sim` as if the designator matters** — Phase 1 PoC hardcodes an LM321 deck; the part-number you pass is **ignored**. Use it as an LM321 deck self-test, NOT as a simulation of the designator you passed.
- **Comparing IC `pins[].name` with passive `pins[].number` directly** — the KB only maintains pin names for ICs. Cross-reference IC pin **names → numbers**, never against passive numbers.
- **Power-context `--with-power` fallback means "not a bug"** — `source_kind: "unknown"` with `source_net_id: null` means the rail is internally generated or injected, NOT a power-tree tracing failure.
- **A capacitor on a switch node is not "optional decoupling"** — if it sits behind a series resistor (2Ω + 4.7nF), the pair is a snubber; the resistor is marked `2R/NC`, so if the resistor is not fitted the capacitor is left floating and the network is dead. Never drop one half of the pair.
- **`NC` in a Comment field means "do not fit"** — a part whose Comment is exactly `NC` (Value blank) is an unpopulated position. `2R/NC` / `10nF/NC` / `1nF/NC` mean "fit this value, or leave NC" — fit both halves of such a pair or neither.

## 9. Board facts

- **Default fixture**: `E:\__electric\__workspace\stacking\circuit-graph\fixtures\protel\FX-T268-V6-10米-5065.NET`
- **T268-V6 project netlist**: `E:\__electric\__workspace\coding\T268-V6\__docs\FX-T268-V6-10米-5065.NET` (identical to the fixture, md5 `3c83ed05…`)
- **SW5001 (Q5/Q7) switch-node snubbers** — every SW node carries a 2Ω + 4.7nF series RC to ground: SW1=R25+C7, SW2=R26+C9, SW3=R39+C31, SW4=R40+C33, plus a 1nF direct to ground (C10/C12/C34/C35) and a 100nF bootstrap (C27/C28/C51/C52).
- **Three parallel power channels, one template each** — every channel is *switching node → big cap bank → diode → 1.5 k → MCU sense pin*:
  | Channel | Stage | Sense node | Sense pin | MCU filter |
  |---|---|---|---|---|
  | A (iPhone) | Q5 SW5001, SW1/SW2 | SW2 → C13 404 µf + C14/C19/C23/C25 104 µf → VBAK_A | D2 → R54 1.5 k | VDMOA (R53 1 k, C55 1 nf) |
  | B (TWS) | Q7 SW5001, SW3/SW4 | SW4 → C36 404 µf + C37/C38/C44/C46 → VBAK_B | D3 → R56 1.5 k | VDMOB (R55 1 k) |
  | C (Apple Watch) | Q2+Q3 PE2312 full bridge, TIM1 CH1/CH1N/CH2/CH2N | NetC5_1 → C5 100 nF NPO (‖C62/C72/C73 = NC) → VBAK → CL1 → NetC74_2 | D1 → R52 1.5 k | VDMO (R51 1 k, R48/R60 100 k, R57 200 k) |
- **Channel C is a series-resonant driver**, not a switching supply: CL1 (L) + the C5 bank (C) form the tank across the bridge midpoints, the tank midpoint is VBAK, and the firmware (`IPHONE_BIAO/sboubiao_pid.c`) sweeps 160/320/330 kHz with a PID loop on `SBPWMCK` (phase/duty). C62/C72/C73 are spare 0805 positions for re-tuning the tank (`f0 = 1/(2π√(LC))`); their Value is blank by design.
- **`.SchDoc` is out of cg's reach** — cg reads `.NET` and `.kicad_sch` only. The one fact cg cannot give is the schematic *Comment* string (`NC`, `2R/NC`, `100nF/NPO`), which lives only in the binary `.SchDoc`. Read it with a throwaway Python script that parses the pipe-records (`RECORD=1` component, `RECORD=34` designator, `RECORD=41` comment, `RECORD=17` net label) — never by hand, never by guessing from the netlist.
