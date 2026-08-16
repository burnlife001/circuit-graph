# atk-logic-capture

Capture and analyze waveforms from ATK-Logic USB logic analyzer.

Drives the official ATK-LogicView GUI through a tiny C# helper
(`tools/atk_bg/atk_bg.exe`) that posts F1/F2/F12 to the Qt window — no DLL
injection, no TCP server, no foreground-window hijack. State persists in a
JSON file under `%APPDATA%\ALIENTEK\ATK-LogicView\skill_state.json`.

See [`SKILL.md`](SKILL.md) for the full capture/analysis workflow and
`scripts/atk_cli.py capture --help` for the CLI surface.