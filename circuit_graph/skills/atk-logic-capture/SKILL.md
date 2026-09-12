---
name: atk-logic-capture
description: Capture and analyze waveforms from ATK-Logic USB logic analyzer. Read .atkdl files, measure frequency/duty/period, identify signal types (UART/square wave/PWM/clock), estimate baud rates. Triggers on: 捕获波形, 采集波形, 分析CH, 波形分析, capture waveform, ATK-Logic, atkdl, 逻辑分析仪, 逻辑分析, analyze channel, 看波形, 测频率, 测占空比, 解码UART, decode uart.
---

# ATK-Logic Capture & Waveform Analyzer

Capture data from ATK-Logic USB logic analyzer and analyze .atkdl waveform files.
**Architecture:** PostMessage + SendMessage to the official Qt GUI via a tiny
C# helper (`atk_bg.exe`). No DLL injection, no fake DLL, no TCP server.

## Scripts Inventory

The skill exposes a single `atk` command that auto-discovers the install
location — see [Find your install](#find-your-install) below.

| Path (relative to skill root) | Purpose |
|------|---------|
| `bin/atk.py` | Self-discovering dispatcher (resolves SCRIPTS_DIR, forwards to atk_cli.py / atk_classify.py / …) |
| `bin/atk` | Bash shim (`exec python $(dirname $0)/atk.py "$@"`) |
| `bin/atk.cmd` | Windows shim (`python "%~dp0atk.py" %*`) |
| `scripts/atk_cli.py` | Main CLI — `info`, `export`, `decode`, `capture`, `config`, `list-decoders`, `where` |
| `scripts/capture/atk_preflight.py` | DLL-free preflight (6 checks + atk_bg.exe + set.ini shortcuts + GUI window) |
| `scripts/analyze/atk_classify.py` | **Main analysis entry** — auto-scans channels, classifies signal types, dispatches to per-protocol analyzers |
| `scripts/_config.py` | Centralized path configuration — GUI, DATA_DIR, atk_bg.exe, log dirs. |
| `scripts/capture_reader.py` | `.atkdl` / `.bin` file reader — `CaptureReader`, `EdgeEvent`, `parse_time()` |
| `scripts/config_sync.py` | Syncs capture params (channels, setHz, threshold) between CLI args and set.ini. Backup-on-change. |
| `scripts/session_state.py` | Persistence of `preflight_ok` + `is_ctrl_s_pressed` to `%APPDATA%\ALIENTEK\ATK-LogicView\skill_state.json`. GUI-session-scoped via `gui_pid` + `gui_start` matching. |
| `scripts/analyze/_proto_common.py` | `ProtoFrame`, `_EdgeHelper`, `normalize_options` shared by native decoders. |

### Per-protocol analyzers (auto-invoked by `atk classify`)
| File | Purpose |
|------|---------|
| `analyze/atk_pwm.py` | PWM / breathing LED analyzer — carrier freq, duty range, modulation |
| `analyze/atk_uart.py` | Native UART decoder — supports baud/data-bits/parity/stop-bits/polarity |
| `analyze/atk_i2c.py` | Native I²C decoder |
| `analyze/atk_spi.py` | Native SPI decoder |

These can also be invoked directly via `atk <protocol> <file.atkdl>`.

### Per-protocol analyzers (auto-invoked by `atk classify`)
| File | Purpose |
|------|---------|
| `analyze/atk_pwm.py` | PWM / breathing LED analyzer — carrier freq, duty range, modulation |
| `analyze/atk_uart.py` | Native UART decoder — supports baud/data-bits/parity/stop-bits/polarity |
| `analyze/atk_i2c.py` | Native I²C decoder |
| `analyze/atk_spi.py` | Native SPI decoder |

These can also be invoked directly via `atk uart|pwm|i2c|spi <file.atkdl>` or `atk decode --decoder <id> <file>`.

### Drivers (C# .NET 4.0)
| File | Purpose |
|------|---------|
| `tools/atk_bg/atk_bg.cs` | Source: PostMessage/SendMessage helper. |
| `tools/atk_bg/atk_bg.exe` | Compiled binary (12.5 KB). Invoked by `atk_cli.py` for F1/F2/F12 key delivery and Save-As dialog handling. |
| `tools/atk_bg/build.ps1` | `pwsh build.ps1` rebuilds via `csc.exe` (v4.0.30319, C# 5). |

### Reference spikes (kept as documented validation — do NOT delete)
| File | Purpose |
|------|---------|
| `capture/spike_e2e.py` | End-to-end spike: F1 → 3s → F2 → 3s → F12 + Save-As dialog fill via raw Win32. Proved the non-disturbing path before productionizing into `atk_bg.exe`. |
| `capture/spike_dialog.py` | Dumps the ATK-Logic Save-As dialog's #32770 child tree (Edit/Button ids/text). Used to confirm control ids `Edit=1001`, `Button id=1 text="保存(&S)"`. |

## Find your install

The `atk` shim auto-discovers the skill — **you never need to remember a path**. Just run `atk <subcmd>`. Discovery order (first hit wins):

| # | Where | When this fires |
|---|-------|-----------------|
| 1 | `$ATK_SCRIPTS_DIR` env var | Manual override (debugging, custom install) |
| 2 | `<bin>/../scripts/atk_cli.py` | Sibling layout — skill installed with its own `bin/` next to `scripts/` |
| 3 | Walk up from cwd: `.claude/skills/atk-logic-capture/scripts/atk_cli.py` | Project-level install (this repo) |
| 4 | `$HOME/.claude/skills/atk-logic-capture/scripts/atk_cli.py` | User-level install (after running `install.ps1`) |

Sanity-check what got picked:

```bash
atk --where
# prints the resolved SCRIPTS_DIR, e.g.
# E:\__electric\__workspace\coding\04_QPnano_LED_Key_ModeSwitch\.claude\skills\atk-logic-capture\scripts
```

If `atk` reports it can't find the skill:
- **bash/pwsh direct invocation works** → run `atk.cmd` / `bash bin/atk` to see if the shim resolves correctly.
- **nothing works** → re-run `install.ps1`, or set `ATK_SCRIPTS_DIR` manually.

### Install locations

- **Project-level (default)**: `<repo>/.claude/skills/atk-logic-capture/`. Tracked in git. Works from anywhere inside the repo via discovery step 3 (walk-up).
- **User-level** (`%USERPROFILE%/.claude/skills/atk-logic-capture/`): created by `install.ps1`. Works from any directory via discovery step 4.
- **PATH shim** (`%USERPROFILE%/bin/atk.cmd` + `atk`): also installed by `install.ps1`. Once your shell is restarted, `atk` resolves from anywhere.

## Key Locations

| Path | Purpose |
|------|---------|
| `D:/Programs/ATK-Logic/ATK-Logic.exe` | Official ATK-Logic GUI (closed source) — **always use this, never other builds** |
| `D:/Programs/ATK-Logic/Qt5Network.dll` | **Original Qt5Network runtime** — must match Qt's expected size (1.3 MB); NOT a proxy DLL. |
| `E:/__electric/atk-logic-data/` | Capture data output (auto-timestamped .atkdl files) |
| `%APPDATA%\ALIENTEK\ATK-LogicView\set.ini` | Capture settings, channel config, keyboard shortcuts (`startCollecting=F1`, `stopCollecting=F2`, `saveFile=F12`), savePath. |
| `%APPDATA%\ALIENTEK\ATK-LogicView\skill_state.json` | Session state — `gui_pid`, `gui_start` (ISO8601), `is_ctrl_s_pressed`, `preflight_ok`. Reset implicitly when GUI restarts. |
| `%APPDATA%\ALIENTEK\ATK-LogicView\log\` | GUI session logs (GBK encoding); the ground-truth surface for "did atk_bg's hotkey actually fire?". |

## Capture Workflow

**Agent behavior**: When the user says "捕获波形" / "capture waveform" without specifying parameters, just run the default capture immediately — no config display, no confirmation prompts. Only ask for clarification if the user explicitly mentions a non-standard scenario.

### Step 1: Capture waveform

Use the `atk` shim (auto-discovers the skill — see [Find your install](#find-your-install)):

```bash
# Default: 3s
atk capture start

# Custom channels + duration
atk capture start --ch 2,3 --duration 10s
```

`cmd_capture` handles preflight automatically — no manual preflight step needed. It consults `skill_state.json`:

- **`preflight_ok == true`** (current GUI session) → skip preflight, go straight to capture.
- **`preflight_ok == false`** or session mismatch → auto-run `atk preflight --fix --json`, then continue.

**Save flow with state-file flag (`is_ctrl_spressed`)**

Before F1, the CLI reads `is_ctrl_s_pressed` from state. The drive sequence is fixed: `F1 → sleep duration → F2 → sleep 3 s → F12`.

After F12, the GUI behavior branches:

- **`is_ctrl_s_pressed == false`** (first save in this GUI session): the GUI opens the Save-As picker. `atk_bg.exe save <path>` then locates the picker, fills the Edit (`id=1001`), clicks the Save Button (`id=1`, text `"保存(&S)"`), and verifies the file lands. On success, the flag is set true.
- **`is_ctrl_s_pressed == true`** (subsequent saves): the GUI direct-saves to its last-used path with no picker. The CLI mtime-scans `DATA_DIR` for the newest `.atkdl` after F12.

> The `--output` filename only matters in mode (a); in mode (b) the GUI chooses the filename and the CLI detects it by mtime. If you need a specific filename, delete `is_ctrl_s_pressed` from the state file before capturing.

### Step 2: If capture fails (stuck at 1% or save picker not found)

The GUI data is still in buffer. Do NOT force-kill — close gracefully:

```bash
# Graceful close: send WM_CLOSE via taskkill without /f flag
taskkill /im ATK-Logic.exe
# Only if it hangs for >3s, use /f as last resort
```

**Why not /f**: `taskkill /f` calls `TerminateProcess()` which skips Qt cleanup, leaving `temp/memory/` markers. On next close or restart, ATK-Logic shows a blocking "恢复数据" dialog.

### Capture parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--ch` | GUI current | Comma-separated channel IDs. Passing this triggers set.ini sync + possible GUI restart. |
| `--duration` | 3s | Capture duration (ns/us/ms/s) |
| `--sample-rate-hz` | GUI current | Sample rate in Hz. Passing this triggers set.ini sync + possible GUI restart. |
| `--threshold` | GUI current | Logic threshold voltage. Passing this triggers set.ini sync + possible GUI restart. |
| `--sync` / `-S` | off | Force sync capture params + savePath to GUI before capture (will restart GUI if values differ). |
| `--rle` | off | FPGA RLE compression |
| `--output` | auto-timestamp | Output filename (mode-a save only). Mode-b save: GUI chooses; CLI detects by mtime. |

### Config management (manual):

```bash
atk config show
atk config set --ch 0,1,2,3 --hz 20000000 --threshold 1.5
atk config set --ch 0,1 --dry-run
```

## Driver: atk_bg.exe (C# .NET 4.0)

The non-disturbing replacement for the previous proxy DLL. Built from
`tools/atk_bg/atk_bg.cs` via `csc.exe` (no managed runtime, just .NET
Framework 4.0 reference assemblies). ~12.5 KB compiled binary.

It talks to the official ATK-Logic GUI window via Win32 messages — *no* keybd_event,
*no* SendInput, *no* SetForegroundWindow, *no* DLL injection. The user's
foreground window stays exactly as it was.

Commands:
- `atk_bg.exe key F1|F2|F12` — locate GUI (`Qt5152QWindowOwnDCIcon` /
  title `ATK-LogicView - DL16`), PostMessage `WM_ACTIVATE(WA_ACTIVE)` +
  `WM_SETFOCUS` to enable Qt's shortcut routing, then PostMessage
  `WM_KEYDOWN` / `WM_KEYUP` with proper lparam (scan code via MapVirtualKeyW).
  Outputs `{"status":"ok","cmd":"key","key":"F1","hwnd":...}` on stdout.
- `atk_bg.exe save <path> [--timeout N]` — wait for the #32770 Save-As
  dialog to be owned by the GUI process (≤N seconds). Walk ComboBoxEx32
  → ComboBox → Edit to find the filename field. `WM_SETTEXT` with the
  target filename, click the Save button (`id=1`, text `"保存(&S)"`),
  click any overwrite-confirm Button (`id=6` or text containing `"是"`).
  Internally normalises `/` → `\` because the picker rejects forward slashes
  with "提示文件名无效".

Both commands are pure-message; neither steals foreground focus from the user.

## State file: `%APPDATA%\ALIENTEK\ATK-LogicView\skill_state.json`

```json
{
  "gui_pid": 15352,
  "gui_start": "2026-08-16T14:30:53.2847735Z",
  "is_ctrl_s_pressed": false,
  "preflight_ok": true
}
```

Lifecycle:
- Written by `atk_preflight.py` on success (sets `preflight_ok=true`) and
  by `atk_cli.py` after first-save succeeds (sets `is_ctrl_s_pressed=true`).
- Read on every CLI invocation; if the running GUI's pid + start time
  don't match the file, all flags default to False.
- To reset: stop ATK-Logic.exe (`taskkill /im ATK-Logic.exe` without `/f`)
  before next capture, or delete the file.

## Analysis Workflow

`atk_classify.py` is the single entry point. It auto-scans channels, classifies
the signal type, and **auto-dispatches** detected protocols to the matching analyzer.

### Step 1: Classify + auto-dispatch (recommended)

```bash
# Auto-scan all channels, dispatch PWM → atk_pwm.py, serial_data → atk_uart.py
atk classify <file.atkdl>

# Specific channels
atk classify <file.atkdl> --ch 0,1 --end 2s

# JSON output (classification + dispatch results embedded)
atk classify <file.atkdl> --ch 4 --json

# Classify only, skip auto-dispatch
atk classify <file.atkdl> --no-dispatch
```

Auto-dispatch behavior:
- `pwm` channels → subprocess `atk_pwm.py` with same file/ch/start/end args
- `serial_data` channels → in-process `UartDecoder` with auto-detected `baud_estimate`
- All other types (clock / square_wave / static / etc.) → stats printed, no dispatch

### Step 2: Manual control (advanced)

```bash
# Run a specific protocol decoder directly (skip classification)
atk decode <file> --decoder uart --rx 0 --option baudrate=115200
atk decode <file> --decoder i2c  --scl 0 --sda 1
atk decode <file> --decoder spi  --clk 0 --mosi 0 --miso 1

# Or call the per-protocol analyzer directly
atk uart <file> --rx 4 --baudrate 115200
atk pwm  <file> --ch 1 --window 200ms

# Export raw edge events
atk export <file> --ch 0,1 --start 0 --end 2s
```

## Path Configuration

All external paths are defined in `scripts/config.ini` and read via `scripts/_config.py` getters. To relocate external dependencies, edit `config.ini`:

```ini
[atk-logic]
DATA_DIR = E:/__electric/atk-logic-data
GUI_DIR = D:/Programs/ATK-Logic
```

```python
from _config import (
    SCRIPTS_DIR,          # self-location (auto-detected)
    SKILL_DIR,            # parent of SCRIPTS_DIR
    ATK_BG_EXE,           # tools/atk_bg/atk_bg.exe (used by atk_cli.py)
    get_data_dir,         # from config.ini DATA_DIR
    get_log_dir,          # get_data_dir() / "logs"
    get_gui_dir,          # from config.ini GUI_DIR
)
```

## Reference: Official ATK-Logic Shortcuts

| Key | Action |
|-----|--------|
| F1 | Start capture (开始采集) — `startCollecting=F1` |
| F2 | Stop capture (停止采集) — `stopCollecting=F2` |
| F12 | Save (保存) — `saveFile=F12` (remapped from Ctrl+S to a single key so PostMessage works without modifier injection). |

Modifiers (`Ctrl+S`) cannot be reliably synthesised via PostMessage because the Qt
app reads physical-key state. Keeping save on a plain F12 is the design constraint.

## Reference: Config

- `%APPDATA%\ALIENTEK\ATK-LogicView\set.ini` — capture settings, channel
  config, keyboard shortcuts, savePath. GUI reads on launch; writes on
  exit / capture. **Closed on save edits** — atk_cli restarts the GUI when
  it has to change a value.
- `%APPDATA%\ALIENTEK\ATK-LogicView\skill_state.json` — atk-cli CLI
  session state. Does not affect GUI behaviour.
- `scripts/config.ini` — paths only (`[atk-logic] DATA_DIR = ...,
  GUI_DIR = ...`). No capture params — those are set via CLI args and
  pushed to `set.ini`.
- Shortcuts are user-configurable via GUI: Functions → Settings → Keyboard Shortcuts.
- `%APPDATA%\ALIENTEK\ATK-LogicView\temp\memory\` — crash-recovery markers
  (left by `/f` kill; triggers "恢复数据" dialog if not cleaned before close/startup).

## Known Issues

1. **FPGA capture stuck at 1%**: If progress never advances, restart the GUI
   and try again. May need USB device power-cycle.

2. **Capture params decision rule**: Follow Step 1 decision table strictly —
   `gui_was_running=true` → capture without `--ch`/`--sample-rate-hz`/
   `--threshold`; `false` → AI passes needed params. Passing params when
   GUI already runs triggers unnecessary GUI kill+restart. Use `--sync` to
   force-sync all params + savePath regardless of GUI state (e.g. after
   manual GUI changes or on first setup).

3. **First save filename is not guaranteed**: When `is_ctrl_s_pressed==false`,
   `atk_bg save <path>` tries to fill the Save-As picker; if the GUI did a
   direct save anyway (e.g., it remembered `savePath` and a default name),
   the CLI falls back to mtime scan and the printed `File:` line may
   differ from `--output`. The actual capture data is correct either way.

4. **GUI forward-slash rejection**: The native `#32770` picker rejects path
   strings with `/` (it answers "提示文件名无效"). `atk_bg save` internally
   normalises `/` → `\` before WM_SETTEXT; callers using `PosixPath` don't
   need to do anything.

5. **Recovery dialog on startup** ✅ Fixed: `taskkill /f` → `TerminateProcess()`
   → skips Qt cleanup → `temp/memory/` + `lock` markers left → "恢复数据"
   dialog blocks next close/startup → forced into another `/f` kill.
   Fix: `_cleanup_unsaved_markers()` runs before `WM_CLOSE` and before GUI launch.

6. **Window class name**: The official ATK-Logic uses window class
   `Qt5152QWindowOwnDCIcon` (verified in spike_dialog.py via EnumWindows).
   Title is `ATK-LogicView - DL16`. atk_bg.exe tries the class first,
   then title-only as fallback.

7. **Pre-paint windows**: Qt creates the QWindow before the MainWindowTitle
   is set. atk_bg requires `gui_running()` (which checks
   `MainWindowTitle -ne ''`) before delivering keys. The CLI uses the
   same condition during `_restart_gui()` to gate startup.

8. **Architecture goal — no DLL injection, no TCP server** ✅ Achieved.
   Previous design deployed a proxy Qt5Network.dll that hosted a TCP server
   on `127.0.0.1:9876`. That DLL has been retired (`skill/proxy_dll/`
   removed from repo); atk_cli now drives the GUI entirely through
   Win32 PostMessage / SendMessage via atk_bg.exe. No code runs inside
   the ATK-Logic process; no extra listen sockets; no interception.

9. **set.ini shortcuts must be single keys (F1/F2/F12) — never modifiers.**
   Modifier combos (`Ctrl+S`, `Ctrl+Shift+S`, etc.) cannot be PostMessage-injected — atk_bg only emits plain F-keys. The GUI ships with `saveFile=Ctrl+S`, which silently breaks the save flow.

   **Fix once per host** (run BEFORE `capture start`):
   ```powershell
   $ini = "$env:APPDATA\ALIENTEK\ATK-LogicView\set.ini"
   Copy-Item $ini "$ini.pre-readonly.bak"
   (Get-Content $ini -Raw) -replace '(?m)^saveFile=Ctrl\+S\s*$','saveFile=F12' |
     Set-Content -Path $ini -Encoding UTF8 -NoNewline
   Set-ItemProperty -Path $ini -Name IsReadOnly -Value $true   # GUI rewrites set.ini on launch — block that
   & "D:/Programs/ATK-Logic/ATK-Logic.exe"
   ```
   To restore user shortcuts later: `Set-ItemProperty ... IsReadOnly $false`, then GUI → Settings → Keyboard Shortcuts.

10. **`atk_preflight.py` flat-search misses bare-section keys.** set.ini puts the shortcut block as bare `key=value` lines at the file tail (no `[section]` header). `_ensure_save_file_shortcut()` only collects keys seen inside a recognised section, so it reports `saveFile='' (expected 'F12')` even after a correct patch. Fix: also collect lines matching `^[A-Za-z][A-Za-z0-9_]*=` before any section header.

11. **GUI rewrites set.ini on launch.** Qt calls `QSettings.sync()` shortly after startup and overwrites any out-of-band edits. External patches must be paired with `IsReadOnly = $true` — otherwise the next capture cycle sees `Ctrl+S` again (see issue 9).

12. **Git Bash quoting hazards on Windows.** The shell is `bash` but every multi-token PowerShell command must be wrapped or written to a file.
    - `taskkill /im ...` — bash treats `/im` as a path → use `powershell -Command "Stop-Process -Name ..."` or a `.ps1` file.
    - `powershell -Command "...$_..."` — `$_` is consumed by bash extglob before PowerShell sees it. Write to a `.ps1` and invoke via `-File`.
    - `python -c "..."` with newlines/`$_`-like tokens — same class of failure. Prefer a tiny `.py` next to the skill scripts over inline `-c`.
    - `cd <path> && python ...` does NOT persist — each Bash call is a fresh shell. Use absolute paths.

13. **Auto-classify GBK decode traceback is cosmetic.** When GUI logs are GBK, `atk_pwm.py` subprocess prints `UnicodeDecodeError: 'utf-8' codec can't decode byte 0xa1`. The classifier's own report above the traceback is unaffected — read it, ignore the traceback. Real fix: pass `encoding='gbk'` to the dispatch Popen.

14. **`atk.cmd` cosmetic 'M is not recognized' warning.** When `atk.cmd` is invoked via `cmd /c` (or via `powershell & atk.cmd`) under pyenv-win's `python.bat` shim, cmd.exe prints `'M' is not recognized as an internal or external command` once before the actual output. The actual command output is unaffected — read past it. Cause: pyenv-win's `python.bat` chain (`chcp 1250 > NUL` + `call pyenv exec %~n0 %*`) emits a stray line during `cmd /c` chain invocations. **Workaround**: prefer `bash atk` or `atk` (after PATH-shim install) on Windows — both bypass the .cmd shim entirely.
