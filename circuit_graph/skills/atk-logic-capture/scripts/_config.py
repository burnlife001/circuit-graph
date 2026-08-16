"""
ATK-Logic Capture — centralized path configuration.
Import this module to get all external paths; nothing else should hardcode paths.
"""
from pathlib import Path

# —— self location ——
SCRIPTS_DIR = Path(__file__).resolve().parent   # .../skills/atk-logic-capture/scripts/
SKILL_DIR = SCRIPTS_DIR.parent                   # .../skills/atk-logic-capture/

# —— atk_bg.exe (background message helper) ——
# Built from tools/atk_bg/atk_bg.cs. Single binary that drives the GUI via
# PostMessage + SendMessage (no DLL injection). If you move atk_bg.exe,
# update this path.
ATK_BG_EXE = SKILL_DIR / "tools" / "atk_bg" / "atk_bg.exe"

# —— ATK-Logic appdata ——
APPDATA_ATK = Path("C:/Users/yg/AppData/Roaming/ALIENTEK/ATK-LogicView")

# —— config.ini ——
CONFIG_INI = SCRIPTS_DIR / "config.ini"


def get_data_dir() -> Path:
    """Read DATA_DIR from config.ini. Returns Path, or hardcoded fallback if missing."""
    if CONFIG_INI.exists():
        text = CONFIG_INI.read_text(encoding="utf-8")
        for line in text.splitlines():
            line_s = line.strip()
            if line_s.lower().startswith("data_dir") and "=" in line_s:
                val = line_s.split("=", 1)[1].strip()
                return Path(val)
    # Fallback — should only trigger if config.ini is missing
    return Path("E:/__electric/atk-logic-data/")


def get_log_dir() -> Path:
    """Return LOG_DIR derived from DATA_DIR."""
    return get_data_dir() / "logs"


def get_gui_dir() -> Path:
    """Read GUI_DIR from config.ini. Returns Path, or hardcoded fallback if missing."""
    if CONFIG_INI.exists():
        text = CONFIG_INI.read_text(encoding="utf-8")
        for line in text.splitlines():
            line_s = line.strip()
            if line_s.lower().startswith("gui_dir") and "=" in line_s:
                val = line_s.split("=", 1)[1].strip()
                return Path(val)
    # Fallback — should only trigger if config.ini is missing
    return Path("D:/Programs/ATK-Logic")
