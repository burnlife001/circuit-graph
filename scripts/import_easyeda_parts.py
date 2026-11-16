"""把 easyeda-agent 的器件快照导入 component KB（离线，不联网）。

数据源是 ``circuit_graph/knowledge/seed/easyeda/`` 下的两份原样快照
（见同目录 ``SOURCE.md``）。只导入**非无源器件**的身份事实（类别 / 描述 /
厂商 / LCSC 号）与引脚表；``deviceUuid``、``footprint`` 不进 KB。

用法::

    python scripts/import_easyeda_parts.py [--db PATH] [--snapshot-dir DIR]
                                           [--dry-run] [--json]

``--dry-run`` 只读库出报告，不写任何行。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from circuit_graph.knowledge.db import ComponentDatabase  # noqa: E402

# 无源器件前缀：这些条目不进 KB（板级被动件由 netlist 自带参数解释）。
PASSIVE_PREFIXES = frozenset({"res", "cap", "ind"})

# 只写显式映射，其余前缀不发明类别词表。
CATEGORY_MAP = {"buck": "dcdc", "buckboost": "dcdc", "ldo": "ldo"}

DEFAULT_SNAPSHOT_DIR = (
    Path(__file__).resolve().parents[1] / "circuit_graph" / "knowledge" / "seed" / "easyeda"
)

# 快照漂移校验：manifest 记录两份 JSON 的 sha256 与上游位置。
MANIFEST_NAME = "manifest.json"
SNAPSHOT_FILES = ("standard-parts.json", "symbol-pins.json")
UPSTREAM_REL_DIR = ".agents/skills/easyeda-agent/references"

# 报告里统计的三个器件事实字段。
FACT_FIELDS = ("category", "description", "manufacturer")


@dataclass(frozen=True)
class PartPlan:
    """单个 MPN 的导入意图。"""

    part_number: str
    slug: str
    category: str | None
    description: str | None
    manufacturer: str | None
    lcsc: str | None
    datasheet_url: str | None
    pins: dict[str, str] | None
    facts: dict[str, str]  # 本次要补的器件事实（已存在器件只含空字段）
    created: bool
    write_pins: bool
    pinout_conflict: bool
    component_id: int | None = None


@dataclass(frozen=True)
class ImportPlan:
    parts: tuple[PartPlan, ...]
    report: dict[str, Any]


def load_snapshot(snapshot_dir: Path | str) -> tuple[dict[str, Any], dict[str, Any]]:
    """读两份快照，返回 ``(parts, symbol_pins)``（都是 slug 键的字典）。"""
    base = Path(snapshot_dir)
    parts_doc = json.loads((base / "standard-parts.json").read_text(encoding="utf-8"))
    pins_doc = json.loads((base / "symbol-pins.json").read_text(encoding="utf-8"))
    return parts_doc.get("parts") or {}, pins_doc.get("parts") or {}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_head(repo: Path) -> str | None:
    try:
        proc = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return proc.stdout.strip() or None


def build_manifest(
    snapshot_dir: Path | str, source_repo: Path | str | None = None
) -> dict[str, Any]:
    """按当前本地快照生成 manifest（sha256 + 字节数 + 上游相对路径）。"""
    base = Path(snapshot_dir)
    files: dict[str, Any] = {}
    for name in SNAPSHOT_FILES:
        path = base / name
        if not path.exists():
            raise SystemExit(f"快照文件缺失：{path}")
        files[name] = {
            "sha256": _sha256(path),
            "bytes": path.stat().st_size,
            "source_path": f"{UPSTREAM_REL_DIR}/{name}",
        }
    manifest: dict[str, Any] = {"files": files}
    if source_repo is not None:
        repo = Path(source_repo)
        manifest["source_repo"] = repo.as_posix()
        manifest["source_commit"] = _git_head(repo)
    return manifest


def check_snapshot(
    snapshot_dir: Path | str, source_repo: Path | str | None = None
) -> tuple[list[str], list[str]]:
    """比对本地快照与 manifest、以及生成快照时的上游内容。

    返回 ``(issues, notes)``：``issues`` 为漂移/损坏（非空即失败），
    ``notes`` 为无法比对的情况（上游仓库不在本机等）。
    """
    base = Path(snapshot_dir)
    manifest_path = base / MANIFEST_NAME
    if not manifest_path.exists():
        return [f"manifest 缺失：{manifest_path}"], []

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    issues: list[str] = []
    notes: list[str] = []
    files = manifest.get("files") or {}

    for name, meta in files.items():
        local = base / name
        if not local.exists():
            issues.append(f"快照文件缺失：{local}")
        elif _sha256(local) != meta.get("sha256"):
            issues.append(f"本地快照已被修改：{name}（与 manifest 不符）")

    source = source_repo or manifest.get("source_repo")
    repo = Path(source) if source else None
    if repo is None or not repo.exists():
        notes.append("未找到上游仓库，只校验本地快照与 manifest")
        return issues, notes

    recorded = manifest.get("source_commit")
    head = _git_head(repo)
    if head and recorded and head != recorded:
        notes.append(f"上游 commit 已变化：{recorded[:7]} → {head[:7]}")
    for name, meta in files.items():
        upstream = repo / str(meta.get("source_path") or "")
        if not upstream.exists():
            notes.append(f"上游缺少 {name}，跳过内容比对")
        elif _sha256(upstream) != meta.get("sha256"):
            issues.append(f"上游 {name} 内容已变化，快照需重新生成")
    return issues, notes


def _slug_prefix(slug: str) -> str:
    return slug.split(".", 1)[0].strip().lower()


def _pin_map(raw: Any) -> dict[str, str]:
    """``[{"n": "1", "name": "VIN"}]`` → ``{"1": "VIN"}``。"""
    out: dict[str, str] = {}
    for pin in raw or []:
        if not isinstance(pin, dict):
            continue
        number = str(pin.get("n") or "").strip()
        name = str(pin.get("name") or "").strip()
        if number and name:
            out[number] = name
    return out


def _text(entry: dict[str, Any], key: str) -> str | None:
    value = entry.get(key)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _existing_index(db: ComponentDatabase) -> dict[str, Any]:
    """``UPPER(part_number) -> ComponentRecord``。

    一表两用：大小写无关地判断器件是否已存在，并取回库内原始大小写用于
    写回——``UNIQUE(part_number)`` 是大小写敏感的，用快照里的写法插入会多出一行。
    """
    return {rec.part_number.upper(): rec for rec in db.query()}


def _has_generic_pinout(db: ComponentDatabase, component_id: int) -> bool:
    """该器件是否已有**通用**引脚表（``source_ref IS NULL``）。

    板级映射（``source_ref`` = 位号）只是某块板上该元件的引脚名，不能代表
    器件本身；它不阻止导入通用表，否则「先导入过板级映射的型号永远补不上
    通用表」，换板后引脚名就会漂移。这与 ``get_pinout`` 的「通用优先、
    板级兜底」一致。
    """
    row = db.conn.execute(
        "SELECT 1 FROM pinouts WHERE component_id = ? AND source_ref IS NULL LIMIT 1",
        (component_id,),
    ).fetchone()
    return row is not None


def _properties_by_id(db: ComponentDatabase, component_id: int) -> dict[str, str]:
    rows = db.conn.execute(
        "SELECT key, value, unit FROM properties WHERE component_id = ?",
        (component_id,),
    ).fetchall()
    return {
        row["key"]: f"{row['value']} {row['unit']}" if row["unit"] else row["value"]
        for row in rows
    }


def plan_import(
    parts: dict[str, Any],
    symbol_pins: dict[str, Any],
    db: ComponentDatabase,
) -> ImportPlan:
    """只读库，算出导入计划与验收报告。"""
    grouped: dict[str, dict[str, Any]] = {}
    passives_skipped = no_mpn_skipped = non_passive_parts = with_mpn = 0
    entries_with_pins = 0

    for slug, entry in parts.items():
        if not isinstance(entry, dict):
            continue
        if _slug_prefix(slug) in PASSIVE_PREFIXES:
            passives_skipped += 1
            continue
        non_passive_parts += 1
        if _pin_map(symbol_pins.get(slug)):
            entries_with_pins += 1
        mpn = _text(entry, "mpn")
        if mpn is None:
            no_mpn_skipped += 1
            continue
        with_mpn += 1
        group = grouped.get(mpn)
        if group is None:
            grouped[mpn] = {
                "slug": slug,
                "entry": entry,
                "pin_slug": slug,
                "pins": _pin_map(symbol_pins.get(slug)),
                "duplicates": [],
            }
            continue
        # 同 MPN 合并：事实取首条；引脚取"有引脚表的那条"。
        group["duplicates"].append(slug)
        if not group["pins"]:
            pins = _pin_map(symbol_pins.get(slug))
            if pins:
                group["pins"] = pins
                group["pin_slug"] = slug

    plans: list[PartPlan] = []
    conflicts: list[dict[str, str]] = []
    duplicate_details: list[dict[str, Any]] = []
    components_new = components_existing = 0
    facts_filled = facts_unchanged = 0
    pinouts_new = pinouts_skipped = 0
    categories_mapped = {"dcdc": 0, "ldo": 0}
    existing = _existing_index(db)

    for mpn, group in grouped.items():
        entry = group["entry"]
        category = CATEGORY_MAP.get(_slug_prefix(group["slug"]))
        if category:
            categories_mapped[category] += 1
        description = _text(entry, "desc")
        manufacturer = _text(entry, "manufacturer")
        snapshot_facts = {
            "category": category,
            "description": description,
            "manufacturer": manufacturer,
        }

        record = existing.get(mpn.upper())
        created = record is None
        # 已存在时沿用库内原始大小写，避免 UNIQUE 约束下多出一行。
        part_number = record.part_number if record is not None else mpn
        facts: dict[str, str] = {}
        if created:
            components_new += 1
            facts = {k: v for k, v in snapshot_facts.items() if v}
        else:
            components_existing += 1
            current = {
                "category": record.category,
                "description": record.description,
                "manufacturer": record.manufacturer,
            }
            for key in FACT_FIELDS:
                value = snapshot_facts[key]
                if current[key]:
                    facts_unchanged += 1
                elif value:
                    facts[key] = value
                    facts_filled += 1

        pins = group["pins"] or None
        conflict = False
        write_pins = False
        if pins:
            if not created and _has_generic_pinout(db, record.id):
                pinouts_skipped += 1
                conflict = True
                conflicts.append(
                    {"part_number": part_number, "reason": "generic_pinout_exists"}
                )
            else:
                write_pins = True
                pinouts_new += 1

        if group["duplicates"]:
            duplicate_details.append(
                {
                    "part_number": part_number,
                    "slugs": [group["slug"], *group["duplicates"]],
                    "pin_slug": group["pin_slug"],
                }
            )

        plans.append(
            PartPlan(
                part_number=part_number,
                slug=group["slug"],
                category=category,
                description=description,
                manufacturer=manufacturer,
                lcsc=_text(entry, "lcsc"),
                datasheet_url=_text(entry, "datasheetUrl"),
                pins=pins,
                facts=facts,
                created=created,
                write_pins=write_pins,
                pinout_conflict=conflict,
                component_id=None if record is None else record.id,
            )
        )

    report: dict[str, Any] = {
        "snapshot": {"parts": len(parts), "symbol_pins": len(symbol_pins)},
        "scope": {
            "non_passive_parts": non_passive_parts,
            "with_mpn": with_mpn,
            "passives_skipped": passives_skipped,
            "no_mpn_skipped": no_mpn_skipped,
            "duplicate_mpn_merged": len(duplicate_details),
            "duplicate_mpn_details": duplicate_details,
        },
        "components": {"new": components_new, "existing": components_existing},
        "facts": {"filled": facts_filled, "unchanged": facts_unchanged},
        "pinouts": {
            "new": pinouts_new,
            # 快照条目口径：无引脚表的非无源条目（重复 slug 也计入）。
            "no_pin_map": non_passive_parts - entries_with_pins,
            "skipped_existing": pinouts_skipped,
        },
        "categories_mapped": categories_mapped,
        "conflicts": conflicts,
    }
    return ImportPlan(parts=tuple(plans), report=report)


def apply_plan(db: ComponentDatabase, plan: ImportPlan) -> dict[str, Any]:
    """执行计划，返回实际写入统计。"""
    applied = {
        "components_created": 0,
        "facts_filled": 0,
        "properties_written": 0,
        "pinouts_written": 0,
    }
    for part in plan.parts:
        if part.facts:
            db.set_component_facts(
                part.part_number,
                category=part.facts.get("category"),
                description=part.facts.get("description"),
                manufacturer=part.facts.get("manufacturer"),
            )
            applied["facts_filled"] += len(part.facts)

        current_props = (
            _properties_by_id(db, part.component_id) if part.component_id is not None else {}
        )
        for key, value in (("lcsc", part.lcsc), ("datasheet_url", part.datasheet_url)):
            if value and current_props.get(key) != value:
                db.set_property(part.part_number, key, value)
                applied["properties_written"] += 1

        if part.write_pins and part.pins:
            db.upsert_resolved_pins(part.part_number, part.pins, pin_source="easyeda")
            applied["pinouts_written"] += len(part.pins)

        if part.created:
            row = db.conn.execute(
                "SELECT 1 FROM components WHERE part_number = ?",
                (part.part_number,),
            ).fetchone()
            if row is not None:
                applied["components_created"] += 1

    return applied


def _print_report(report: dict[str, Any]) -> None:
    snap = report["snapshot"]
    scope = report["scope"]
    comps = report["components"]
    facts = report["facts"]
    pins = report["pinouts"]
    print(f"snapshot: parts={snap['parts']} symbol_pins={snap['symbol_pins']}")
    print(
        "scope: non_passive={0} with_mpn={1} passives_skipped={2} "
        "no_mpn_skipped={3} duplicate_mpn_merged={4}".format(
            scope["non_passive_parts"],
            scope["with_mpn"],
            scope["passives_skipped"],
            scope["no_mpn_skipped"],
            scope["duplicate_mpn_merged"],
        )
    )
    for detail in scope["duplicate_mpn_details"]:
        print(f"  merged {detail['part_number']}: {', '.join(detail['slugs'])}"
              f" (pins from {detail['pin_slug']})")
    print(f"components: new={comps['new']} existing={comps['existing']}")
    print(f"facts: filled={facts['filled']} unchanged={facts['unchanged']}")
    print(
        "pinouts: new={0} no_pin_map={1} skipped_existing={2}".format(
            pins["new"], pins["no_pin_map"], pins["skipped_existing"]
        )
    )
    cats = ", ".join(f"{k}={v}" for k, v in report["categories_mapped"].items())
    print(f"categories_mapped: {cats}")
    for conflict in report["conflicts"]:
        print(f"  conflict {conflict['part_number']}: {conflict['reason']}")
    applied = report.get("applied")
    if applied is not None:
        print(
            "applied: components_created={0} facts_filled={1} "
            "properties_written={2} pinouts_written={3}".format(
                applied["components_created"],
                applied["facts_filled"],
                applied["properties_written"],
                applied["pinouts_written"],
            )
        )
    else:
        print("dry-run: nothing written")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Import the easyeda-agent part snapshot into the component KB.",
    )
    ap.add_argument("--db", default=None, help="SQLite KB path (default: bundled circuit_graph.db)")
    ap.add_argument("--snapshot-dir", default=None, help="directory holding the two JSON snapshots")
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    ap.add_argument("--json", action="store_true", dest="as_json", help="emit a JSON report")
    ap.add_argument(
        "--check-snapshot",
        action="store_true",
        help="只校验快照与 manifest/上游是否一致，不导入、不连库",
    )
    ap.add_argument(
        "--update-manifest",
        action="store_true",
        help="按当前本地快照重写 manifest（重新取快照后执行一次）",
    )
    ap.add_argument(
        "--source-repo",
        default=None,
        help="easyeda-agent 仓库路径（默认取 manifest 里记录的）",
    )
    args = ap.parse_args(argv)

    snapshot_dir = Path(args.snapshot_dir) if args.snapshot_dir else DEFAULT_SNAPSHOT_DIR
    if args.update_manifest:
        manifest = build_manifest(snapshot_dir, args.source_repo)
        target = snapshot_dir / MANIFEST_NAME
        target.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(f"manifest written: {target}")
        return 0
    if args.check_snapshot:
        issues, notes = check_snapshot(snapshot_dir, args.source_repo)
        for note in notes:
            print(f"note: {note}")
        for issue in issues:
            print(f"ISSUE: {issue}")
        print("snapshot ok" if not issues else f"snapshot drift: {len(issues)} issue(s)")
        return 1 if issues else 0

    parts, symbol_pins = load_snapshot(snapshot_dir)
    db = ComponentDatabase.default() if args.db is None else ComponentDatabase.open(Path(args.db))
    try:
        plan = plan_import(parts, symbol_pins, db)
        report = dict(plan.report)
        report["dry_run"] = bool(args.dry_run)
        if not args.dry_run:
            report["applied"] = apply_plan(db, plan)
    finally:
        db.close()

    if args.as_json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
