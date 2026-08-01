# ATK-Logic Capture

CLI-based capture & waveform analysis for the ATK-Logic USB logic analyzer (ALIENTEK), deployed as a [Claude Code](https://claude.com/claude-code) skill.

## Quick Start

```powershell
# One-shot deploy
pwsh install.ps1

# Capture 2 seconds on CH0+CH1
python ~/.claude/skills/atk-logic-capture/scripts/atk_cli.py capture start --ch 0,1 --duration 2s

# Analyze captured waveform (auto-dispatches PWM / UART analyzers)
python ~/.claude/skills/atk-logic-capture/scripts/atk_classify.py <file.atkdl>
```

## Prerequisites

- Windows 10/11 x64
- PowerShell 7+
- Python 3.12+ (on PATH)
- ATK-Logic USB logic analyzer hardware
- ATK-Logic GUI installed (from ALIENTEK)

## What install.ps1 Does

| Step | Action |
|------|--------|
| 1 | Locates `~/.claude/skills/` (searches multiple paths, creates if missing) |
| 2 | Copies the entire skill tree (skipping `__pycache__`, `.codegraph`, `.git`) |
| 3 | Finds ATK-Logic GUI across 8+ standard install paths |
| 4 | Deploys proxy DLL: backs up `Qt5Network.dll` → `Qt5Network_real.dll`, then installs the proxy |
| 5 | Updates `scripts/_config.py` with local paths (GUI, data, appdata) |
| 6 | Installs the sole Python dependency: `uiautomation` |
| 7 | Runs preflight self-check (`atk_preflight.py --fix`) |

## Directory Layout

```
~/.claude/skills/atk-logic-capture/
├── SKILL.md              # Skill manifest (Claude Code)
├── README.md             # This file
├── install.ps1           # One-shot deployment script
├── proxy_dll/
│   ├── Qt5Network.dll    # Pre-built proxy DLL (~307 KB)
│   └── src/              # Proxy source + build script (MSVC required)
│       ├── proxy_main.c
│       ├── build.ps1
│       └── Qt5Network_proxy.def
└── scripts/
    ├── _config.py         # Centralized path configuration
    ├── capture_reader.py  # .atkdl / .bin file reader
    ├── config_sync.py     # set.ini read/write with backup
    ├── atk_cli.py         # Main CLI: info, export, decode, capture, config, list-decoders
    ├── uia_save.py        # UIA automation for Save As dialog
    ├── capture/
    │   └── atk_preflight.py   # Pre-flight self-check (with --fix auto-repair)
    ├── analyze/
    │   ├── _proto_common.py   # ProtoFrame / EdgeHelper shared by native decoders
    │   ├── atk_classify.py    # Main analysis entry: classify + auto-dispatch
    │   ├── atk_pwm.py         # PWM / breathing LED analyzer
    │   ├── atk_uart.py        # Native UART decoder
    │   ├── atk_i2c.py         # Native I²C decoder
    │   └── atk_spi.py         # Native SPI decoder
```

## Manual Deploy

If `install.ps1` doesn't fit your environment, follow these steps:

### 1. Copy skill directory

Copy the entire `atk-logic-capture/` folder to `C:/Users/<用户名>/.claude/skills/atk-logic-capture/`.
Skip `__pycache__/`, `.codegraph/`, `.git/`.

### 2. Install Python dependency

```powershell
pip install uiautomation
```

### 3. Deploy proxy DLL

In the ATK-Logic install directory:

```powershell
ren Qt5Network.dll Qt5Network_real.dll
copy <skill-dir>/proxy_dll/Qt5Network.dll .
```

If no pre-built DLL, build with MSVC: `cd proxy_dll/src && pwsh build.ps1`

### 4. Edit config

Edit `scripts/_config.py`:

```python
GUI_EXE  = Path("D:/Programs/ATK-Logic/ATK-Logic.exe")
GUI_DIR  = Path("D:/Programs/ATK-Logic")
DATA_DIR = Path("E:/__electric/atk-logic-data/")
LOG_DIR  = Path("E:/__electric/atk-logic-data/logs/")
```

### 5. Verify

```powershell
python ~/.claude/skills/atk-logic-capture/scripts/atk_preflight.py --fix
```

All 4 checks PASS = ready.

## License

MIT
