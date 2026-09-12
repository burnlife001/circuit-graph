#!/usr/bin/env python
"""atk — self-discovering dispatcher for atk-logic-capture skill.

Locates the skill installation and forwards commands to the right entry point.
Designed to be wrapped by `bin/atk` (bash) and `bin/atk.cmd` (cmd/pwsh).

Discovery order (first hit wins):
    1. $ATK_SCRIPTS_DIR env var
    2. Sibling layout: <bin>/../scripts/atk_cli.py
    3. Walk up from cwd: <dir>/.claude/skills/atk-logic-capture/scripts/atk_cli.py
    4. User-level:     $HOME/.claude/skills/atk-logic-capture/scripts/atk_cli.py

Command dispatch:
    --where / --scripts-dir    Print SCRIPTS_DIR and exit
    classify                   → analyze/atk_classify.py
    preflight                  → capture/atk_preflight.py
    uart|pwm|i2c|spi           → analyze/atk_<proto>.py
    <anything-else>            → atk_cli.py  (capture, info, export, decode, config, …)
"""
import os
import sys
from pathlib import Path


def find_scripts_dir() -> Path | None:
    env = os.environ.get("ATK_SCRIPTS_DIR")
    if env:
        cand = Path(env) / "atk_cli.py"
        if cand.is_file():
            return cand.parent.resolve()

    here = Path(__file__).resolve().parent
    sibling = here.parent / "scripts" / "atk_cli.py"
    if sibling.is_file():
        return sibling.parent.resolve()

    cwd = Path.cwd().resolve()
    for p in [cwd, *cwd.parents]:
        cand = p / ".claude/skills/atk-logic-capture/scripts/atk_cli.py"
        if cand.is_file():
            return cand.parent.resolve()

    home = Path.home()
    user = home / ".claude/skills/atk-logic-capture/scripts/atk_cli.py"
    if user.is_file():
        return user.parent.resolve()

    return None


_SUBCOMMAND_DISPATCH = {
    "classify": "analyze/atk_classify.py",
    "preflight": "capture/atk_preflight.py",
    "uart": "analyze/atk_uart.py",
    "pwm": "analyze/atk_pwm.py",
    "i2c": "analyze/atk_i2c.py",
    "spi": "analyze/atk_spi.py",
}


def main() -> int:
    if len(sys.argv) <= 1 or sys.argv[1] in ("--where", "--scripts-dir"):
        scripts_dir = find_scripts_dir()
        if scripts_dir is None:
            print(
                "atk: cannot locate atk-logic-capture skill.\n"
                "  Searched: $ATK_SCRIPTS_DIR, sibling layout, "
                "walk-up from cwd, $HOME/.claude/skills/.\n"
                "  Fix: run install.ps1, or set ATK_SCRIPTS_DIR.",
                file=sys.stderr,
            )
            return 127
        print(scripts_dir)
        return 0

    cmd, *rest = sys.argv[1:]
    scripts_dir = find_scripts_dir()
    if scripts_dir is None:
        print(
            "atk: cannot locate atk-logic-capture skill.\n"
            "  Searched: $ATK_SCRIPTS_DIR, sibling layout, "
            "walk-up from cwd, $HOME/.claude/skills/.\n"
            "  Fix: run install.ps1, or set ATK_SCRIPTS_DIR.",
            file=sys.stderr,
        )
        return 127

    target = _SUBCOMMAND_DISPATCH.get(cmd)
    if target is not None:
        os.execvp("python", ["python", str(scripts_dir / target), *rest])

    os.execvp("python", ["python", str(scripts_dir / "atk_cli.py"), *sys.argv[1:]])
    return 0  # unreachable


if __name__ == "__main__":
    sys.exit(main())
