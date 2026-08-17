"""Generate primitives/index_primitives.md from primitives/*.json.

Manual run:
    uv run python scripts/build_primitive_index.py            # regenerate
    uv run python scripts/build_primitive_index.py --check   # exit 1 if stale

Used by .pre-commit-config.yaml hook; also covered by a pytest test so CI
still catches drift if the hook is missing locally.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PRIMITIVES_DIR = REPO_ROOT / "primitives"
INDEX_PATH = PRIMITIVES_DIR / "index_primitives.md"
SKIP_FILES = {"schema.json"}

EXPECTED_KEYS_HUMAN = {
    "expected_role": "role",
    "expected_direction": "direction",
    "expected_level": "level",
}


def _collect_signals(raw: dict) -> list[dict]:
    return list(raw.get("signals", []))


def _strict_modes(signals: list[dict]) -> list[str]:
    """Return human-readable list of declared expected_* constraints."""
    modes: set[str] = set()
    for sig in signals:
        for key in EXPECTED_KEYS_HUMAN:
            if sig.get(key) is not None:
                modes.add(EXPECTED_KEYS_HUMAN[key])
    return sorted(modes)


def _truncate(s: str, n: int) -> str:
    s = s.strip()
    if len(s) <= n:
        return s
    return s[: n - 1] + "…"


def collect_primitives(primitives_dir: Path) -> list[dict]:
    rows: list[dict] = []
    errors: list[str] = []
    for path in sorted(primitives_dir.glob("*.json")):
        if path.name in SKIP_FILES:
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}: invalid JSON ({exc.msg} at line {exc.lineno})")
            continue
        if "id" not in raw:
            errors.append(f"{path.name}: missing required field 'id'")
            continue
        rows.append(
            {
                "id": raw["id"],
                "name": raw.get("name", raw["id"]),
                "description": _truncate(raw.get("description", ""), 60),
                "modes": _strict_modes(_collect_signals(raw)),
                "file": path.name,
            }
        )
    if errors:
        raise SystemExit("\n".join(errors))
    rows.sort(key=lambda r: r["id"])
    return rows


def render_markdown(rows: list[dict]) -> str:
    today = date.today().isoformat()
    header = [
        "# Primitives 索引",
        "",
        f"> 由 [`scripts/build_primitive_index.py`](./scripts/build_primitive_index.py) "
        f"自动生成于 {today}，共 {len(rows)} 个基元。请勿手改。",
        "",
        "| ID | 名称 | 用途 | 严格模式 | 文件 |",
        "|---|---|---|---|---|",
    ]
    body = [
        "| `{id}` | {name} | {description} | {modes} | "
        "[{file}](./{file}) |".format(
            id=row["id"],
            name=row["name"],
            description=row["description"] or "—",
            modes=", ".join(row["modes"]) if row["modes"] else "—",
            file=row["file"],
        )
        for row in rows
    ]
    footer = [
        "",
        "## 新增基元",
        "",
        "在 `primitives/` 目录新增一个 `<id>.json`，pre-commit hook 会自动重生成此文件。",
        "若未安装 hook，可手动跑 `uv run python scripts/build_primitive_index.py`。",
        "",
    ]
    return "\n".join(header + body + footer)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 if index is stale (used by CI/test).",
    )
    args = parser.parse_args()

    rows = collect_primitives(PRIMITIVES_DIR)
    expected = render_markdown(rows)

    if args.check:
        actual = INDEX_PATH.read_text(encoding="utf-8") if INDEX_PATH.exists() else ""
        if actual != expected:
            sys.stderr.write(
                "primitives/index_primitives.md is out of date.\n"
                "Run: uv run python scripts/build_primitive_index.py\n"
            )
            return 1
        return 0

    INDEX_PATH.write_text(expected, encoding="utf-8")
    print(f"Wrote {INDEX_PATH.relative_to(REPO_ROOT)} ({len(rows)} entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
