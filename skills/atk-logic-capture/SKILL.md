---
name: atk-logic-capture
description: Capture and analyze waveforms from ATK-Logic USB logic analyzer. Read .atkdl files, measure frequency/duty/period, identify signal types (UART/square wave/PWM/clock), estimate baud rates. Triggers on: 捕获波形, 采集波形, 分析CH, 波形分析, capture waveform, ATK-Logic, atkdl, 逻辑分析仪, 逻辑分析, analyze channel, 看波形, 测频率, 测占空比, 解码UART, decode uart.
---

# ATK-Logic Capture & Waveform Analyzer

Capture data from ATK-Logic USB logic analyzer and analyze .atkdl waveform files.

## Scripts Inventory

SKILL_SCRIPTS = `C:/Users/yg/.claude/skills/atk-logic-capture/scripts/`

### Configuration & Bootstrap
| File | Purpose |
|------|---------|
| `__init__.py` | Package marker |
| `_config.py` | Centralized path configuration — GUI, DLL, data/log dirs, TCP settings. **Edit this file to relocate dependencies.** |

### Shared modules (scripts/)
| File | Purpose |
|------|---------|
| `capture_reader.py` | `.atkdl` / `.bin` file reader — `CaptureReader`, `EdgeEvent`, `parse_time()` |
| `config_sync.py` | Syncs capture params (channels, setHz, threshold) between config.ini and set.ini with backup. |
| `analyze/_proto_common.py` | `ProtoFrame`, `_EdgeHelper`, `normalize_options` shared by all native decoders. |

### User-facing Entry Points
| File | Purpose |
|------|---------|
| `atk_cli.py` | Main CLI — `info`, `export`, `decode`, `capture`, `config`, `list-decoders` |
| `capture/atk_preflight.py` | Pre-flight self-check (6 items) with `--fix` auto-repair |
| `capture/uia_save.py` | UIA automation companion — fills filename + clicks Save in native save picker |
| `analyze/atk_classify.py` | **Main analysis entry** — auto-scans channels, classifies signal types, auto-dispatches to per-protocol analyzers |

### Per-protocol analyzers (auto-invoked by `atk_classify.py`)
| File | Purpose |
|------|---------|
| `analyze/atk_pwm.py` | PWM / breathing LED analyzer — carrier freq, duty range, modulation detection |
| `analyze/atk_uart.py` | Native UART decoder — supports baud/data-bits/parity/stop-bits/polarity |
| `analyze/atk_i2c.py` | Native I²C decoder |
| `analyze/atk_spi.py` | Native SPI decoder |

These can also be invoked directly, or via `atk_cli.py decode --decoder <uart|i2c|spi>`.

### Dependency Graph
```
_config.py ────► atk_cli.py, capture/atk_preflight.py, capture/uia_save.py
config_sync.py ─► atk_cli.py, capture/atk_preflight.py
capture_reader.py ──► atk_cli.py, analyze/atk_classify.py, analyze/atk_pwm.py, analyze/atk_uart.py, analyze/atk_i2c.py, analyze/atk_spi.py
capture/uia_save.py ◄─── atk_cli.py (subprocess)
analyze/atk_pwm.py ◄─── analyze/atk_classify.py (auto-dispatch via subprocess)
analyze/atk_uart.py ◄─── analyze/atk_classify.py (auto-dispatch via in-process import)
```

## Key Locations

| Path | Purpose |
|------|---------|
| `D:/Programs/ATK-Logic/ATK-Logic.exe` | Official ATK-Logic GUI (closed source) — **always use this, never other builds** |
| `D:/Programs/ATK-Logic/Qt5Network.dll` | Proxy DLL deployed via DLL hijacking (compiled from `E:/__electric/atk-logic/proxy_dll/`) |
| `E:/__electric/atk-logic-data/` | Capture data output (auto-timestamped .atkdl files) |
| `E:/__electric/atk-logic-data/logs/` | UIA save debug logs |

## Capture Workflow

**Agent behavior**: When the user says "捕获波形" / "capture waveform" without specifying parameters, just run the default capture immediately — no config display, no confirmation prompts. Only ask for clarification if the user explicitly mentions a non-standard scenario (e.g., "special setup", "custom settings").

### Step 1: Capture waveform

`cmd_capture` handles preflight automatically — no manual preflight step needed. It queries the DLL-persisted `g_preflight_ok` flag:
- **Already set** → skips preflight (validated earlier in this GUI session)
- **Not set** → auto-runs `atk_preflight.py --fix --json` before capturing

Just pass the channels the user requested and duration:

```bash
# Default: 3s
python C:/Users/yg/.claude/skills/atk-logic-capture/scripts/atk_cli.py capture start

# Custom channels + duration
python C:/Users/yg/.claude/skills/atk-logic-capture/scripts/atk_cli.py capture start --ch 2,3 --duration 10s
```

**Save flow (config sync is opt-in)** (optimized for speed):

Before capture, the CLI:
1. Reads `config.ini` → `trgDir` (default save directory)
2. Reads current GUI state from `set.ini` for informational display (channels, rate, threshold)
3. **If `--sync` flag or explicit `--ch`/`--sample-rate-hz`/`--threshold` are passed**: syncs the specified params to `set.ini`, syncs `savePath` to `trgDir`, and restarts GUI if needed so changes take effect
4. **If no config params are passed**: skips all `set.ini` modification — captures with whatever the GUI currently has configured (no restart)
5. Queries `isCtrlSPressed` state from Qt5Network.dll via TCP

During capture, the proxy DLL:
- F1 → start capture
- F2 → stop capture (after duration)
- **Ctrl+S** (not Ctrl+Shift+S) → save

After capture, the CLI branches on `isCtrlSPressed`:
- **First save** (`isCtrlSPressed == false`): Ctrl+S opens "Save As" picker → `uia_save.py` fills `--output` filename → sets `isCtrlSPressed = true` in DLL → verifies file exists at expected path
- **Subsequent saves** (`isCtrlSPressed == true`): Ctrl+S saves directly to GUI's last-used path (no picker). The `--output` filename is **not used** — instead, the CLI records the newest `.atkdl` mtime *before* capture, then finds the file that appeared after capture. This is the real output file.

The `isCtrlSPressed` flag survives within one ATK-Logic session (reset on restart). This eliminates the UIA picker-filling step for all saves after the first, significantly speeding up repeated captures.

**Important: `--output` filename** only applies to the first save (picker case). For subsequent saves, the GUI chooses the filename; the CLI detects it by scanning for new files in `trgDir`. The printed `Save name:` line is informational for the first-save case only.

### Step 2: If capture fails (stuck at 1% or save picker not found)

The GUI data is still in buffer. Do NOT force-kill — close gracefully instead:

```bash
# Graceful close: send WM_CLOSE via taskkill without /f flag
taskkill /im ATK-Logic.exe
# Only if it hangs for >3s, use /f as last resort
```

**Why not /f**: `taskkill /f` calls `TerminateProcess()` which skips Qt cleanup, leaving `temp/memory/` markers. On next close or restart, ATK-Logic shows a blocking "恢复数据" dialog. The code in `_restart_gui()` now auto-cleans these markers before `WM_CLOSE` to break this cycle.

### Capture parameters:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `--ch` | GUI current | Comma-separated channel IDs. Passing this triggers set.ini sync + possible GUI restart. |
| `--duration` | 3s | Capture duration (ns/us/ms/s) |
| `--sample-rate-hz` | GUI current | Sample rate (Hz). Passing this triggers set.ini sync + possible GUI restart. |
| `--threshold` | GUI current | Logic threshold voltage. Passing this triggers set.ini sync + possible GUI restart. |
| `--sync` / `-S` | off | Force sync capture params + savePath to GUI before capture (will restart GUI if values differ). |
| `--rle` | off | FPGA RLE compression |
| `--output` | auto-timestamp | Output filename (first-save picker only). Subsequent saves: GUI chooses name, CLI detects newest file in trgDir. |

### Config management (manual):

```bash
# Read current set.ini params
python .../atk_cli.py config show

# Write params to set.ini (backs up first if changes needed)
python .../atk_cli.py config set --ch 0,1,2,3 --hz 20000000 --threshold 1.5

# Dry-run: preview what would change
python .../atk_cli.py config set --ch 0,1 --dry-run
```

## Analysis Workflow

`atk_classify.py` is the single entry point. It auto-scans channels, classifies the signal type, and **auto-dispatches** detected protocols to the matching analyzer.

**Note on Windows**: use the full path — `~` does NOT expand in `python ~/...` commands.

### Step 1: Classify + auto-dispatch (recommended)

```bash
# Auto-scan all channels, dispatch PWM → atk_pwm.py, serial_data → atk_uart.py
python C:/Users/yg/.claude/skills/atk-logic-capture/scripts/analyze/atk_classify.py <file.atkdl>

# Specific channels
python .../atk_classify.py <file.atkdl> --ch 0,1 --end 2s

# JSON output (classification + dispatch results embedded)
python .../atk_classify.py <file.atkdl> --ch 4 --json

# Classify only, skip auto-dispatch
python .../atk_classify.py <file.atkdl> --no-dispatch
```

Auto-dispatch behavior:
- `pwm` channels → subprocess `atk_pwm.py` with same file/ch/start/end args
- `serial_data` channels → in-process `UartDecoder` with auto-detected `baud_estimate`
- All other types (clock / square_wave / static / etc.) → stats printed, no dispatch

### Step 2: Manual control (advanced)

```bash
# Run a specific protocol decoder directly (skip classification)
python .../atk_cli.py decode <file> --decoder uart --rx 0 --option baudrate=115200
python .../atk_cli.py decode <file> --decoder i2c  --scl 0 --sda 1
python .../atk_cli.py decode <file> --decoder spi  --clk 0 --mosi 1

# Or call the decoder script directly
python .../analyze/atk_uart.py <file> --rx 4 --baudrate 115200
python .../analyze/atk_pwm.py <file> --ch 1 --window 200ms

# Export raw edge events
python .../atk_cli.py export <file> --ch 0,1 --start 0 --end 2s
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
    get_data_dir,         # from config.ini DATA_DIR
    get_log_dir,          # get_data_dir() / "logs"
    get_gui_dir,          # from config.ini GUI_DIR
    TCP_HOST,             # 127.0.0.1
    TCP_PORT,             # 9876
)
```

## Reference: Official ATK-Logic Shortcuts

| Key | Action |
|-----|--------|
| F1 | Start capture (开始采集) |
| F2 | Stop capture (停止采集) |
| Ctrl+S | Save (保存) — proxy DLL uses this. First save opens Save As picker, subsequent saves overwrite in-place. |

## TCP API (Qt5Network.dll proxy)

| Command | Description |
|---------|-------------|
| `{"cmd":"start",...}` | Start capture: F1 → wait → F2 → Ctrl+S |
| `{"cmd":"stop"}` | Stop capture: F2 |
| `{"cmd":"status"}` | Get capturing state |
| `{"cmd":"file"}` | Find newest .atkdl in WATCH_DIR |
| `{"cmd":"get_isCtrlSPressed"}` | Get isCtrlSPressed state → `{"status":"ok","value":"0"|"1"}` |
| `{"cmd":"set_isCtrlSPressed","value":0|1}` | Set isCtrlSPressed state |
| `{"cmd":"get_preflight_status"}` | Get preflight status → `{"status":"ok","value":"0"|"1"}` |
| `{"cmd":"set_preflight_status","value":0|1}` | Set preflight status (persists until DLL unload) |

## Reference: Config

- `%APPDATA%\ALIENTEK\ATK-LogicView\set.ini` — capture settings, channel config, keyboard shortcuts, savePath
- `scripts/config.ini` — paths only (`[atk-logic] DATA_DIR = ..., GUI_DIR = ...`). No capture params — those are set via CLI args and pushed to `set.ini`.
- Shortcuts are user-configurable via GUI: Functions → Settings → Keyboard Shortcuts
- `%APPDATA%\ALIENTEK\ATK-LogicView\temp\memory\` — crash-recovery markers (left by `/f` kill; triggers "恢复数据" dialog if not cleaned before close/startup)

## Known Issues

1. **FPGA capture stuck at 1%**: If progress never advances, restart the GUI and try again. May need USB device power-cycle.

2. **Capture params decision rule**: Follow Step 1 decision table strictly — `gui_was_running=true` → capture without `--ch`/`--sample-rate-hz`/`--threshold`; `false` → AI passes needed params. Passing params when GUI already runs triggers unnecessary GUI kill+restart. Use `--sync` to force-sync all params + savePath regardless of GUI state (e.g. after manual GUI changes or on first setup).

3. **Ctrl+S save behavior**: The proxy DLL uses Ctrl+S (standard Save). First save per session opens "Save As" picker (handled by `uia_save.py`). Subsequent saves overwrite directly — if the GUI uses a QML FileDialog instead of the native `#32770` picker, the first-save detection will fail with `save_prompt: "none"` and the file won't be saved at the correct path.

4. **Recovery dialog on startup** ✅ Fixed: `taskkill /f` → `TerminateProcess()` → skips Qt cleanup → `temp/memory/` + `lock` markers left → "恢复数据" dialog blocks next close/startup → forced into another `/f` kill (vicious cycle). Fix: `_cleanup_unsaved_markers()` runs before `WM_CLOSE` and before GUI launch, removing the markers so the dialog never appears.

5. **Window class name**: ~~The official ATK-Logic uses window class `Qt5152QWindowOwnDCIcon`, NOT `FramelessWindow_QML_51`.~~ **✅ Fixed**: proxy DLL source updated to `Qt5152QWindowOwnDCIcon` + recompiled.

6. **Full path pasted into Save As picker**: uia_save.py pastes the full output path into the filename field. Windows Save As picker accepts this, so the file lands directly at the target location. No copy step needed.

7. **TCP-ready ≠ GUI-ready race**: The DLL's TCP server starts in `DllMain` (process attach), which runs BEFORE Qt creates the main window. `_restart_gui()` now waits for both TCP connect AND a visible GUI window (`MainWindowTitle != ''`) before returning, plus a 1s settle time. This prevents F1 keybd_event from landing on nothing and producing "Capture complete (no new .atkdl found)".

8. **Preflight status reset on restart**: `_restart_gui()` reloads the DLL, which resets `g_preflight_ok` to 0 in `DllMain`. `cmd_capture()` now tracks `gui_restarted` and re-runs preflight after restart if needed.
