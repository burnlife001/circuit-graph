"""Template WebUI — testable core (no HTTP). Serves template_server.py + tests."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIXTURES_DIR = _REPO_ROOT / "fixtures"
_PRIMITIVES_DIR = _REPO_ROOT / "primitives"
_SCHEMA_PATH = _PRIMITIVES_DIR / "schema.json"

PRIMITIVE_ID_RE = re.compile(r"^[a-z0-9-]+$")
BOARD_NAME_RE = re.compile(r"^[^/\\]+$")

VALUE_TYPE_PATTERNS: dict[str, str | None] = {
    "zero_ohm_jumper": (
        r"^(?:0+(?:\.0+)?(?:m?[RΩ])?(?:[-_].*)?%?\d*|0\.\d+(?:[RΩ]|$))$"
    ),
    "bootstrap_resistor_1_2ohm": r"^[12](?:\.\d+)?\s*[RΩ](?!\d)",
    "cap_nf": r"^\d+(\.\d+)?\s*(n|nF|nf)\b",
    "any": None,
}

DEFAULT_SCORING = {
    "weights": {
        "structural": 0.35,
        "naming": 0.25,
        "parametric": 0.20,
        "type": 0.20,
    },
    "thresholds": {"high": 0.80, "medium": 0.50, "low": 0.0},
}

TYPE_BY_DEVICE_KIND = {
    "resistor": "Resistor",
    "capacitor": "Capacitor",
    "inductor": "Inductor",
    "ic": "IC",
    "mosfet": "Mosfet",
    "diode": "Diode",
    "bridge_rectifier": "BridgeRectifier",
    "connector": "Connector",
    "unknown": "Unknown",
}


class TemplateWebError(Exception):
    """Domain error with HTTP-ish code."""

    def __init__(self, message: str, *, status: int = 400, code: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def repo_root() -> Path:
    return _REPO_ROOT


def primitives_dir() -> Path:
    return _PRIMITIVES_DIR


def fixtures_dir() -> Path:
    return _FIXTURES_DIR


def validate_primitive_id(primitive_id: str) -> None:
    if not primitive_id or not PRIMITIVE_ID_RE.match(primitive_id):
        raise TemplateWebError("invalid primitive id", status=400, code="invalid_id")


def validate_board_name(board: str) -> None:
    if not board or ".." in board or not BOARD_NAME_RE.match(board):
        raise TemplateWebError("invalid board name", status=400, code="invalid_board")


def resolve_board_path(board: str, *, fixtures: Path | None = None) -> Path:
    validate_board_name(board)
    root = fixtures or _FIXTURES_DIR
    matches = [p for p in root.rglob("*.NET") if p.is_file() and p.name == board]
    if not matches:
        raise TemplateWebError("board not found", status=404, code="board_not_found")
    if len(matches) > 1:
        # Prefer protel fixture when ambiguous
        protel = [p for p in matches if "protel" in p.as_posix()]
        return protel[0] if protel else matches[0]
    return matches[0]


def find_primitive_json_path(primitive_id: str, *, primitives: Path | None = None) -> Path:
    validate_primitive_id(primitive_id)
    base = primitives or _PRIMITIVES_DIR
    for path in sorted(base.rglob("*.json")):
        if path.name == "schema.json":
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if raw.get("id") == primitive_id:
            return path
    raise TemplateWebError("primitive not found", status=404, code="not_found")


def load_primitive_raw(primitive_id: str, *, primitives: Path | None = None) -> dict[str, Any]:
    path = find_primitive_json_path(primitive_id, primitives=primitives)
    return json.loads(path.read_text(encoding="utf-8"))


def list_primitives_summary(*, primitives: Path | None = None) -> list[dict[str, Any]]:
    base = primitives or _PRIMITIVES_DIR
    rows: list[dict[str, Any]] = []
    for path in sorted(base.rglob("*.json")):
        if path.name == "schema.json":
            continue
        raw = json.loads(path.read_text(encoding="utf-8"))
        pid = raw.get("id")
        if not pid:
            continue
        rows.append(
            {
                "id": pid,
                "name": raw.get("name", pid),
                "version": raw.get("version", ""),
                "role": raw.get("role", ""),
                "member_count": len(raw.get("components", [])),
                "file": path.relative_to(base).as_posix(),
            }
        )
    rows.sort(key=lambda r: r["id"])
    return rows


def build_render_model(raw: dict[str, Any]) -> dict[str, Any]:
    return {
        "signals": raw.get("signals", []),
        "components": raw.get("components", []),
        "constraints": raw.get("constraints", {}),
        "scoring": raw.get("scoring", {}),
        "symmetry": raw.get("symmetry"),
    }


def list_boards(*, fixtures: Path | None = None) -> list[str]:
    root = fixtures or _FIXTURES_DIR
    return sorted({p.name for p in root.rglob("*.NET") if p.is_file()})


def _parse_board_ir(board_path: Path, *, db_path: Path | None = None):
    from circuit_graph.cli.app import _parse_and_enrich, build_pin_category_provider

    return _parse_and_enrich(
        board_path,
        pin_category_provider=build_pin_category_provider(db_path),
    )


def list_board_components(
    board: str,
    *,
    fixtures: Path | None = None,
    db_path: Path | None = None,
) -> list[dict[str, Any]]:
    path = resolve_board_path(board, fixtures=fixtures)
    ir = _parse_board_ir(path, db_path=db_path)

    from circuit_graph.generators.cg_graph import _format_component_value

    rows: list[dict[str, Any]] = []
    for comp in sorted(ir.components, key=lambda c: c.id):
        value = _format_component_value(comp) or comp.raw_value or ""
        rows.append(
            {
                "id": comp.id,
                "type": comp.device_kind.value.lower(),
                "value": value,
                "part_number": comp.raw_value or value,
            }
        )
    return rows


def _conn_section(ir, designator: str) -> dict[str, Any]:
    from circuit_graph.cli.query import _canonical, _comp_summary, _CONN_MEMBER_CAP

    pin_to_net = {c.pin_id: c.net_id for c in ir.connections}
    net_members: dict[str, set[str]] = {}
    for c in ir.connections:
        comp_id = c.pin_id.rsplit(".", 1)[0]
        net_members.setdefault(c.net_id, set()).add(comp_id)
    comp_map = {c.id: c for c in ir.components}
    net_map = {n.id: n for n in ir.nets}

    from circuit_graph.generators.cg_graph import _format_component_value

    def _value_text(comp) -> str:
        return _format_component_value(comp) or comp.raw_value or "-"

    comp = comp_map.get(_canonical(ir, designator))
    if comp is None:
        raise TemplateWebError(f"component not found: {designator}", status=404)

    pin_rows = []
    for pin in comp.pins:
        net_id = pin_to_net.get(pin.id)
        net_name = net_map[net_id].name if net_id in net_map else None
        members = []
        if net_id is not None:
            for cid in sorted(net_members.get(net_id, ())):
                member = comp_map.get(cid)
                if member is None:
                    continue
                members.append(
                    {
                        "id": cid,
                        "value": _value_text(member),
                        "pins_on_net": sorted(
                            p.number
                            for p in member.pins
                            if pin_to_net.get(p.id) == net_id
                        ),
                    }
                )
        truncated = max(0, len(members) - _CONN_MEMBER_CAP)
        if truncated:
            members = members[:_CONN_MEMBER_CAP]
        pin_rows.append(
            {
                "pin": pin.number,
                "name": pin.name,
                "net": net_name,
                "members": members,
                "truncated": truncated,
            }
        )
    return {"component": _comp_summary(comp), "pins": pin_rows}


def board_pin_neighbors(
    board: str,
    part: str,
    *,
    fixtures: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    validate_board_name(board)
    if not part or ".." in part or "/" in part or "\\" in part:
        raise TemplateWebError("invalid part", status=400)
    path = resolve_board_path(board, fixtures=fixtures)
    ir = _parse_board_ir(path, db_path=db_path)
    return _conn_section(ir, part)


def board_kb_pinout(
    board: str,
    part: str,
    db,
    *,
    fixtures: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    validate_board_name(board)
    if not part or ".." in part or "/" in part or "\\" in part:
        raise TemplateWebError("invalid part", status=400)
    path = resolve_board_path(board, fixtures=fixtures)
    ir = _parse_board_ir(path, db_path=db_path)
    from circuit_graph.cli.query import _canonical

    comp_map = {c.id: c for c in ir.components}
    comp = comp_map.get(_canonical(ir, part))
    if comp is None:
        raise TemplateWebError(f"component not found: {part}", status=404)
    from circuit_graph.generators.cg_graph import _format_component_value

    pn = comp.raw_value or _format_component_value(comp) or ""
    pinout = db.get_pinout(pn, source_ref=part) if pn else None
    if pinout is None and pn:
        pinout = db.get_pinout(pn)
    return {
        "designator": comp.id,
        "part_number": pn,
        "pinout": pinout or {},
        "board_pins": [{"pin": p.number, "name": p.name} for p in comp.pins],
    }


def _template_of_block(block_id: str, template_ids: list[str]) -> str | None:
    from circuit_graph.cli.primitives import _template_of_block as _totb

    return _totb(block_id, template_ids)


def match_board(
    board: str,
    *,
    template_id: str | None = None,
    fixtures: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    validate_board_name(board)
    if template_id is not None:
        validate_primitive_id(template_id)
    path = resolve_board_path(board, fixtures=fixtures)
    ir = _parse_board_ir(path, db_path=db_path)

    from circuit_graph.primitives import load_primitives

    templates = load_primitives(primitives_dir())
    template_ids = [t.id for t in templates]
    names = {t.id: t.name for t in templates}

    known_components = {c.id for c in ir.components}
    blocks_out: list[dict[str, Any]] = []
    for block in ir.blocks:
        tid = _template_of_block(block.id, template_ids)
        if tid is None:
            continue
        if template_id is not None and tid != template_id:
            continue
        blocks_out.append(
            {
                "block_id": block.id,
                "template_id": tid,
                "template_name": names.get(tid, tid),
                "components": sorted(block.component_ids),
            }
        )

    all_members: set[str] = set()
    for b in blocks_out:
        all_members.update(b["components"])

    return {
        "board": board,
        "netlist": path.as_posix(),
        "template_id": template_id,
        "blocks": blocks_out,
        "matched_components": sorted(all_members & known_components),
    }


def load_schema(*, primitives: Path | None = None) -> dict[str, Any]:
    base = primitives or _PRIMITIVES_DIR
    return json.loads((base / "schema.json").read_text(encoding="utf-8"))


def validate_primitive_schema(doc: dict[str, Any], *, primitives: Path | None = None) -> None:
    try:
        import jsonschema
    except ImportError as exc:
        raise TemplateWebError("jsonschema not installed", status=500) from exc
    schema = load_schema(primitives=primitives)
    jsonschema.validate(doc, schema)


def validate_template_payload(
    doc: dict[str, Any],
    *,
    board: str | None = None,
    fixtures: Path | None = None,
    db_path: Path | None = None,
    primitives: Path | None = None,
) -> dict[str, Any]:
    validate_primitive_schema(doc, primitives=primitives)
    out: dict[str, Any] = {"schema_ok": True, "match": None}
    if board:
        hits = match_primitive_on_board(doc, board, fixtures=fixtures, db_path=db_path)
        out["match"] = hits
    return out


def match_primitive_on_board(
    doc: dict[str, Any],
    board: str,
    *,
    fixtures: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """Replay the *submitted* template document against the board.

    match_board() only reads templates from the on-disk library, so replaying a
    draft by id would validate the old file instead of the proposed change.
    This runs the same enricher chain with a temporary primitives dir holding
    exactly the submitted document.
    """
    tid = doc.get("id")
    if not isinstance(tid, str):
        raise TemplateWebError("draft missing id")
    validate_primitive_id(tid)
    validate_board_name(board)
    path = resolve_board_path(board, fixtures=fixtures)

    from circuit_graph.cli.app import build_pin_category_provider
    from circuit_graph.enrichers import run_enrichers
    from circuit_graph.enrichers.edge_semantic import EdgeSemanticEnricher
    from circuit_graph.enrichers.multi_unit_splitter import MultiUnitSplitter
    from circuit_graph.enrichers.net_classifier import NetClassifier
    from circuit_graph.enrichers.pin_resolver import PinNameResolver
    from circuit_graph.enrichers.topology_recognizer import TopologyRecognizer
    from circuit_graph.enrichers.value_normalizer import ValueNormalizer
    from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

    with tempfile.TemporaryDirectory(prefix="cg-template-") as tmp:
        tdir = Path(tmp)
        (tdir / f"{tid}.json").write_text(
            json.dumps(doc, ensure_ascii=False), encoding="utf-8"
        )
        ir = ProtelNetParser().parse(ParserInput(file_path=str(path)))
        enrichers = [
            MultiUnitSplitter(),
            ValueNormalizer(),
            NetClassifier(),
            PinNameResolver(),
            EdgeSemanticEnricher(),
            TopologyRecognizer(
                primitives_dir=tdir,
                pin_category_provider=build_pin_category_provider(db_path),
            ),
        ]
        ir2 = run_enrichers(ir, enrichers).ir

    blocks_out = [
        {"block_id": b.id, "template_id": tid, "components": sorted(b.component_ids)}
        for b in ir2.blocks
        if _template_of_block(b.id, [tid]) == tid
    ]
    members = {c for b in blocks_out for c in b["components"]}
    known = {c.id for c in ir2.components}
    return {
        "board": board,
        "netlist": path.as_posix(),
        "template_id": tid,
        "blocks": blocks_out,
        "matched_components": sorted(members & known),
    }


def _signal_id(net_name: str) -> str:
    return "net_" + re.sub(r"[^a-z0-9]+", "_", net_name.strip().lower()).strip("_")


_GND_LIKE_NETS = {"GND", "CGND", "PGND", "AGND", "DGND", "VSS"}
_GND_NET_PATTERN = r"^(GND|CGND|PGND|AGND|DGND|VSS)$"


def _net_pattern(net_name: str) -> str:
    return _GND_NET_PATTERN if net_name.upper() in _GND_LIKE_NETS else ".*"


def _slugify(text: str) -> str:
    lowered = text.strip().lower()
    out = re.sub(r"[^a-z0-9]+", "-", lowered)
    return out.strip("-") or "template"


def generate_draft(payload: dict[str, Any]) -> dict[str, Any]:
    """Board-driven draft from chip + annotated slots (no disk write)."""
    board = payload.get("board")
    chip = payload.get("chip")
    slots = payload.get("slots") or []
    function = str(payload.get("function") or "").strip()
    shared_signals = payload.get("shared_signals") or []

    if not board or not chip:
        raise TemplateWebError("board and chip required")

    validate_board_name(str(board))
    path = resolve_board_path(str(board))
    ir = _parse_board_ir(path)

    conn = _conn_section(ir, str(chip))
    comp_meta = conn["component"]
    wanted_ids = {str(chip)}
    for slot in slots:
        des = slot.get("designator")
        if des:
            wanted_ids.add(str(des))

    pin_nets: dict[str, dict[str, str]] = {}
    comp_types: dict[str, str] = {}
    for des in wanted_ids:
        try:
            sec = _conn_section(ir, des)
        except TemplateWebError:
            continue
        cid = sec["component"]["id"]
        nets_map = {}
        for row in sec["pins"]:
            if row.get("net"):
                nets_map[str(row["pin"])] = str(row["net"])
        pin_nets[cid] = nets_map
        comp_types[cid] = str(sec["component"].get("type", "unknown"))

    # Merge explicit shared signal ids (bst_mid etc.)
    signal_alias: dict[str, str] = {}
    for item in shared_signals:
        sid = item.get("id")
        if not sid:
            continue
        for net_name in item.get("net_names") or []:
            signal_alias[str(net_name)] = str(sid)

    net_to_comps: dict[str, list[str]] = {}
    for cid, nets in pin_nets.items():
        for net_name in nets.values():
            if not net_name:
                continue
            net_to_comps.setdefault(net_name, []).append(cid)
    shared = {n: cs for n, cs in net_to_comps.items() if len(set(cs)) >= 2}

    signal_ids: dict[str, str] = {}
    for net_name in sorted(shared) + sorted(set(net_to_comps) - set(shared)):
        signal_ids[net_name] = signal_alias.get(net_name) or _signal_id(net_name)

    # Drafts must stay board-agnostic: internal nets use ".*"; only ground-like
    # nets get the canonical pattern. Board net names are never baked in.
    chip_net_names = set(pin_nets.get(str(comp_meta.get("id")), {}).values())
    signals = [
        {
            "id": signal_ids[n],
            "type": "net",
            "name_pattern": _net_pattern(n),
            "required": n in chip_net_names,
        }
        for n in signal_ids
    ]

    ordered_slots: list[dict[str, Any]] = [
        {"designator": chip, **(payload.get("chip_slot") or {})}
    ]
    for slot in slots:
        if str(slot.get("designator")) == str(chip):
            continue
        ordered_slots.append(slot)

    components: list[dict[str, Any]] = []
    for slot in ordered_slots:
        des = str(slot.get("designator") or chip)
        if des not in pin_nets:
            try:
                _conn_section(ir, des)
            except TemplateWebError as exc:
                raise TemplateWebError(f"slot {des}: {exc}") from exc
            sec = _conn_section(ir, des)
            pin_nets[des] = {
                str(r["pin"]): str(r["net"])
                for r in sec["pins"]
                if r.get("net")
            }
        dev_kind = comp_types.get(des, "unknown")
        comp_type = TYPE_BY_DEVICE_KIND.get(dev_kind, "Unknown")
        member_id = slot.get("member_id") or des
        entry: dict[str, Any] = {
            "id": str(member_id),
            "type": comp_type,
            "required": bool(slot.get("required", des == chip)),
            "pins": [
                {
                    "number": pin_number,
                    "net": signal_ids.get(net_name, _signal_id(net_name)),
                }
                for pin_number, net_name in pin_nets.get(des, {}).items()
            ],
        }
        if slot.get("title"):
            entry["title"] = slot["title"]
        if slot.get("optional_group"):
            entry["optional_group"] = slot["optional_group"]
        if slot.get("optional_pin"):
            for p in entry["pins"]:
                if str(p["number"]) == str(slot["optional_pin"]):
                    p["optional"] = True
        vt = slot.get("value_type")
        if vt:
            pat = VALUE_TYPE_PATTERNS.get(str(vt))
            if pat:
                entry["value_pattern"] = pat
                entry["strict_value"] = True
        if slot.get("value_pattern"):
            entry["value_pattern"] = slot["value_pattern"]
            entry["strict_value"] = bool(slot.get("strict_value", True))
        components.append(entry)

    slug = _slugify(function) if function else f"draft-{'-'.join(sorted(wanted_ids)).lower()}"
    name = function or f"{chip}+draft 模板（待人审）"
    draft_id = payload.get("id") or f"draft-{slug}"

    draft: dict[str, Any] = {
        "$schema": "./schema.json",
        "id": draft_id,
        "name": name,
        "description": payload.get("description")
        or f"WebUI 草稿：{path.name} 上 {', '.join(sorted(wanted_ids))}，待人审",
        "version": str(payload.get("version") or "0.1.0"),
        "strict_pin_matching": bool(payload.get("strict_pin_matching", True)),
        "signals": signals,
        "components": components,
        "constraints": payload.get("constraints")
        or {"min_components": len(components), "exact_component_count": False},
        "scoring": payload.get("scoring") or deepcopy(DEFAULT_SCORING),
    }
    if payload.get("role"):
        draft["role"] = payload["role"]

    validate_primitive_schema(draft)

    checklist = [
        "人审 id / name / 每个 signal 的 name_pattern",
        "补 expected_role / expected_ic_pin 等负例约束（防误报优先）",
        "写到 primitives/<类目>/ 后 POST /api/index/rebuild",
        "补 tests/test_primitives_on_fixtures.py CASE",
        f"手跑 pytest：{_REPO_ROOT.as_posix()}/.venv/Scripts/python.exe -m pytest -q",
    ]
    return {"draft": draft, "checklist": checklist, "board": board, "chip": chip}


# ---------------------------------------------------------------------------
# Auto draft — board + chip only, no hand-written internal fields
# ---------------------------------------------------------------------------

# Nets with more members than this are buses (GND rails, power rails): only a
# signal is derived from them, never members.  Keeps the draft from flooding.
_AUTO_MEMBER_CAP = 8
# More matched components than this on replay means the template swallowed the
# whole board — flagged as a conflict instead of being reported as "ready".
_AUTO_FLOOD_CAP = 50
# Optional members are grouped per chip net; the matcher enumerates
# 2**groups variants and silently degrades to "chip only" above 64.
_AUTO_MAX_OPTIONAL_GROUPS = 6
# Protel auto-generated net names ("NetC70_1") are board specific and must
# never leak into a template (id or pattern).
_BOARD_NET_RE = re.compile(r"^Net[A-Za-z]*\d+_\d+$")
_AUTO_SUFFIX_BY_KIND = {
    "capacitor": "cap",
    "resistor": "res",
    "inductor": "ind",
    "diode": "diode",
    "ic": "ic",
    "mosfet": "fet",
    "bridge_rectifier": "bridge",
    "connector": "conn",
    "unknown": "part",
}


def _auto_slug(text: str) -> str:
    """Lowercase slug safe for signal / member ids."""
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").strip().lower()).strip("_")


def _auto_unique_id(base: str, used: set[str]) -> str:
    """Return *base* (or ``base_2`` / ``base_3`` …) not yet present in *used*."""
    if base not in used:
        used.add(base)
        return base
    idx = 2
    while f"{base}_{idx}" in used:
        idx += 1
    name = f"{base}_{idx}"
    used.add(name)
    return name


def _auto_net_signal_id(net_name: str, pin_no: str) -> str:
    """Signal id for a net without a usable pin name.

    Board specific nets ("NetC70_1") would leak the board layout into the
    template, so they fall back to the pin number instead.
    """
    if _BOARD_NET_RE.match(net_name):
        return f"pin_{pin_no}"
    return _signal_id(net_name)


def _auto_value_pattern(comp) -> str | None:
    """Regex for the component's own value, or ``None`` when it has none.

    Values are component properties (not board specific names), so they are
    safe to keep in a template — and they pin a member slot to exactly one
    part, which is what keeps the replay exact and fast.
    """
    if comp is None:
        return None
    raw = str(getattr(comp, "raw_value", "") or "").strip()
    if not raw:
        return None
    return re.escape(raw)


def _auto_pin_names(
    chip_comp,
    conn_rows: list[dict[str, Any]],
    part_number: str,
    db,
) -> tuple[dict[str, str], str]:
    """Resolve pin names for the chip.

    Fallback chain: KB pinout → PDF datasheet (Camelot) → netlist pin names.
    Returns ``({pin_number: pin_name}, source)`` with source in
    ``{"kb", "pdf", "netlist"}``.
    """
    netlist_names = {
        str(row.get("pin")): str(row.get("name") or "") for row in conn_rows
    }

    if part_number and db is not None:
        try:
            pinout = db.get_pinout(part_number)
        except Exception:  # pragma: no cover - KB must never break the draft
            pinout = None
        if pinout:
            merged = {
                pin: str(pinout.get(pin) or netlist_names.get(pin) or "")
                for pin in netlist_names
            }
            if any(merged.values()):
                return merged, "kb"

    if part_number:
        try:
            from circuit_graph.knowledge.pdf_pin_extract import extract_from_datasheet

            pinout = extract_from_datasheet(part_number)
        except Exception:  # pragma: no cover - PDF layout failures are expected
            pinout = None
        if pinout:
            merged = {
                pin: str(pinout.get(pin) or netlist_names.get(pin) or "")
                for pin in netlist_names
            }
            if any(merged.values()):
                return merged, "pdf"

    return netlist_names, "netlist"


def auto_draft(
    board: str,
    chip: str,
    *,
    fixtures: Path | None = None,
    db=None,
    db_path: Path | None = None,
    precise_values: bool = True,
) -> dict[str, Any]:
    """Board-driven draft from *board* + *chip* only (no hand-written fields).

    Everything the old UI asked the user to type — member ids, shared
    signals, optional groups — is derived from the board itself and then
    replayed against it.  The result is a *candidate*: it is reported as
    ``ready`` only when the replay matches cleanly.

    Board specific net names never enter the template: net patterns are
    ``.*`` (or the canonical ground pattern) and ids are slugified pin names.
    """
    validate_board_name(str(board))
    chip = str(chip).strip()
    if not chip or "/" in chip or "\\" in chip or ".." in chip:
        raise TemplateWebError("invalid chip designator", status=400, code="invalid_chip")

    path = resolve_board_path(str(board), fixtures=fixtures)
    ir = _parse_board_ir(path, db_path=db_path)

    from circuit_graph.generators.cg_graph import _format_component_value

    conn = _conn_section(ir, chip)
    chip_id = str(conn["component"]["id"])
    comp_map = {c.id: c for c in ir.components}
    chip_comp = comp_map[chip_id]
    part_number = chip_comp.raw_value or _format_component_value(chip_comp) or ""

    if db is None:
        try:
            from circuit_graph.knowledge.db import ComponentDatabase

            db = ComponentDatabase.default()
        except Exception:  # pragma: no cover - KB is optional
            db = None

    pin_names, pin_source = _auto_pin_names(chip_comp, conn["pins"], part_number, db)

    # ---- net graph (full membership, not the truncated UI view) ----------
    net_name_of = {n.id: n.name for n in ir.nets}
    members_by_net: dict[str, set[str]] = {}
    for c in ir.connections:
        if c.net_id is None:
            continue
        comp_id = c.pin_id.rsplit(".", 1)[0]
        name = net_name_of.get(c.net_id, c.net_id)
        members_by_net.setdefault(str(name), set()).add(comp_id)

    # ---- chip pin → signal ----------------------------------------------
    chip_pin_rows: list[tuple[str, str, str]] = []
    for row in conn["pins"]:
        net = row.get("net")
        if not net:
            continue
        pin_no = str(row.get("pin"))
        name = pin_names.get(pin_no) or row.get("name") or ""
        chip_pin_rows.append((pin_no, str(name or ""), str(net)))

    signals: list[dict[str, Any]] = []
    signal_ids: set[str] = set()
    signal_by_net: dict[str, str] = {}
    gnd_signal_id: str | None = None

    signal_by_base: dict[str, str] = {}
    for pin_no, pin_name, net in chip_pin_rows:
        if net in signal_by_net:
            continue
        base = _auto_slug(pin_name)
        if not base:
            base = _auto_net_signal_id(net, pin_no)
        pattern = _net_pattern(net)
        existing = signal_by_base.get(base)
        if existing is not None:
            # Same pin name on several nets: merge all ground-like nets into
            # one "gnd" signal, disambiguate anything else.
            if pattern == _GND_NET_PATTERN:
                sid = existing
            else:
                sid = _auto_unique_id(base, signal_ids)
                signal_by_base[base] = sid
                signals.append(
                    {
                        "id": sid,
                        "type": "net",
                        "name_pattern": pattern,
                        "required": True,
                    }
                )
                signal_by_net[net] = sid
                continue
        else:
            sid = _auto_unique_id(base, signal_ids)
            signal_by_base[base] = sid
        signal_by_net[net] = sid
        signals.append(
            {"id": sid, "type": "net", "name_pattern": pattern, "required": True}
        )
        if pattern == _GND_NET_PATTERN and gnd_signal_id is None:
            gnd_signal_id = sid

    # ---- member discovery ------------------------------------------------
    member_source_net: dict[str, str] = {}
    for _pin_no, _pin_name, net in chip_pin_rows:
        candidates = members_by_net.get(net, set()) - {chip_id}
        if len(candidates) > _AUTO_MEMBER_CAP:
            continue  # bus / rail: signal only, no members
        for des in sorted(candidates):
            member_source_net.setdefault(des, net)

    # ---- member → signals ------------------------------------------------
    new_net_counter = 0
    member_pins: dict[str, list[dict[str, Any]]] = {}
    skipped_members: list[str] = []

    for des in sorted(member_source_net):
        try:
            sec = _conn_section(ir, des)
        except TemplateWebError:
            skipped_members.append(des)
            continue
        pins: list[dict[str, Any]] = []
        complete = True
        for row in sec["pins"]:
            net = row.get("net")
            if not net:
                complete = False
                break
            net = str(net)
            if net in signal_by_net:
                sid = signal_by_net[net]
            elif _net_pattern(net) == _GND_NET_PATTERN:
                if gnd_signal_id is None:
                    gnd_signal_id = _auto_unique_id("gnd", signal_ids)
                    signal_by_net[net] = gnd_signal_id
                    signals.append(
                        {
                            "id": gnd_signal_id,
                            "type": "net",
                            "name_pattern": _GND_NET_PATTERN,
                            "required": False,
                        }
                    )
                sid = gnd_signal_id
            else:
                new_net_counter += 1
                sid = _auto_unique_id(f"net_{new_net_counter}", signal_ids)
                signal_by_net[net] = sid
                signals.append(
                    {"id": sid, "type": "net", "name_pattern": ".*", "required": False}
                )
            pins.append({"number": row["pin"], "net": sid})
        if not complete or not pins:
            skipped_members.append(des)
            continue
        member_pins[des] = pins

    # ---- components ------------------------------------------------------
    components: list[dict[str, Any]] = []

    chip_entry: dict[str, Any] = {
        "id": "chip",
        "type": TYPE_BY_DEVICE_KIND.get(str(conn["component"].get("type", "unknown")), "Unknown"),
        "required": True,
        "pins": [
            {"number": pin_no, "net": signal_by_net[net]}
            for pin_no, _pin_name, net in chip_pin_rows
        ],
    }
    if part_number and re.fullmatch(r"[A-Za-z0-9._+-]+", part_number):
        chip_entry["value_pattern"] = re.escape(part_number)
        chip_entry["strict_value"] = True
    elif precise_values:
        chip_pattern = _auto_value_pattern(chip_comp)
        if chip_pattern:
            chip_entry["value_pattern"] = chip_pattern
            chip_entry["strict_value"] = True
    components.append(chip_entry)

    member_ids: set[str] = set()
    slots: list[dict[str, Any]] = []

    # Optional members are grouped per chip net: members of one net are
    # fitted or dropped together (like a power stage).  The matcher
    # enumerates 2**groups variants, so the group count is capped — more
    # groups would silently degrade to "chip only" matching.
    groups: dict[str, list[str]] = {}
    for des in sorted(member_pins):
        primary = signal_by_net.get(member_source_net[des], "net")
        groups.setdefault(primary, []).append(des)

    ranked_groups = sorted(groups.items(), key=lambda kv: (len(kv[1]), kv[0]))
    kept_groups = ranked_groups[:_AUTO_MAX_OPTIONAL_GROUPS]
    dropped_groups = ranked_groups[_AUTO_MAX_OPTIONAL_GROUPS :]
    dropped_members = [des for _g, des_list in dropped_groups for des in des_list]

    for primary, des_list in kept_groups:
        for des in des_list:
            member_comp = comp_map.get(des)
            kind = (
                member_comp.device_kind.value.lower()
                if member_comp is not None
                else "unknown"
            )
            suffix = _AUTO_SUFFIX_BY_KIND.get(kind, "part")
            mid = _auto_unique_id(f"{primary}_{suffix}", member_ids)
            comp_type = TYPE_BY_DEVICE_KIND.get(kind, "Unknown")
            entry: dict[str, Any] = {
                "id": mid,
                "type": comp_type,
                "required": False,
                "optional_group": primary,
                "pins": member_pins[des],
            }
            # The reference board's own value pins the slot to one part.  It
            # keeps the replay exact (and fast) — a human relaxes it to a
            # value *type* before promoting the draft into the library.
            if precise_values:
                pattern = _auto_value_pattern(member_comp)
                if pattern:
                    entry["value_pattern"] = pattern
                    entry["strict_value"] = True
            components.append(entry)
            slots.append(
                {
                    "designator": des,
                    "member_id": mid,
                    "type": comp_type,
                    "optional_group": primary,
                    "title": f"{primary} 网上的 {kind}",
                }
            )

    # ---- document --------------------------------------------------------
    slug = _slugify(part_number) if part_number and _slugify(part_number) != "template" else "auto"
    draft_id = f"draft-{chip.lower()}-{slug}"
    draft: dict[str, Any] = {
        "$schema": "./schema.json",
        "id": draft_id,
        "name": f"{chip} 自动草稿",
        "description": (
            f"自动生成：{chip} 的引脚同网成员推导（板名不入库），"
            "待回放/人确认（成员为候选，勿直接入库）"
        ),
        "version": "0.1.0",
        "strict_pin_matching": True,
        "signals": signals,
        "components": components,
        "constraints": {"min_components": 1, "exact_component_count": False},
        "scoring": deepcopy(DEFAULT_SCORING),
    }
    validate_primitive_schema(draft)

    # ---- replay + conflicts ---------------------------------------------
    replay = match_primitive_on_board(draft, str(board), fixtures=fixtures, db_path=db_path)
    matched = list(replay.get("matched_components") or [])
    blocks = list(replay.get("blocks") or [])

    conflicts: list[str] = []
    if not blocks:
        conflicts.append("no_match")
    if len(matched) > _AUTO_FLOOD_CAP:
        conflicts.append(f"flood:{len(matched)}")
    if dropped_members:
        conflicts.append(f"member_overflow:{len(dropped_members)}")

    expected = {chip_id} | set(member_pins)
    ground_devices = {
        des
        for net, des_list in members_by_net.items()
        if _net_pattern(net) == _GND_NET_PATTERN
        for des in des_list
    }
    for des in matched:
        if des in expected:
            continue
        if des in ground_devices:
            continue  # ground bus devices are intentionally not members
        conflicts.append(f"unexpected:{des}")

    state = "ready" if (blocks and matched and not conflicts) else "needs_review"

    checklist = [
        "人审 id / name / 每个 signal 的 name_pattern",
        "确认成员候选（自动推导，可能漏件或多件）",
        "补 expected_role / expected_ic_pin 等负例约束（防误报优先）",
        "写到 primitives/<类目>/ 后 POST /api/index/rebuild",
        "补 tests/test_primitives_on_fixtures.py CASE",
        f"手跑 pytest：{_REPO_ROOT.as_posix()}/.venv/Scripts/python.exe -m pytest -q",
    ]

    return {
        "draft": draft,
        "checklist": checklist,
        "board": board,
        "chip": chip,
        "slots": slots,
        "replay": replay,
        "conflicts": conflicts,
        "state": state,
        "pin_source": pin_source,
        "skipped_members": skipped_members,
    }


def _strip_version(doc: dict[str, Any]) -> dict[str, Any]:
    out = deepcopy(doc)
    out.pop("version", None)
    return out


def assert_version_bumped(
    existing: dict[str, Any] | None,
    new_doc: dict[str, Any],
    *,
    confirm_unchanged_version: bool = False,
) -> None:
    if existing is None:
        return
    old_v = existing.get("version")
    new_v = new_doc.get("version")
    if _strip_version(existing) == _strip_version(new_doc):
        return
    if old_v == new_v and not confirm_unchanged_version:
        raise TemplateWebError(
            "behavior changed but version unchanged; bump version or set confirm_unchanged_version",
            status=400,
            code="version_unchanged",
        )


def resolve_primitive_save_path(
    rel_file: str,
    *,
    primitives: Path | None = None,
) -> Path:
    if not rel_file or ".." in rel_file.replace("\\", "/").split("/"):
        raise TemplateWebError("invalid save path", status=400, code="invalid_path")
    rel = Path(rel_file)
    if rel.is_absolute() or rel.parts and rel.parts[0] == "..":
        raise TemplateWebError("invalid save path", status=400, code="invalid_path")
    base = (primitives or _PRIMITIVES_DIR).resolve()
    target = (base / rel).resolve()
    if not str(target).startswith(str(base)):
        raise TemplateWebError("path outside primitives/", status=403, code="outside_whitelist")
    if target.suffix.lower() != ".json":
        raise TemplateWebError(
            "only .json under primitives/", status=400, code="invalid_suffix"
        )
    return target


def atomic_write_json(target: Path, doc: dict[str, Any]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(doc, indent=2, ensure_ascii=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        dir=target.parent, prefix=target.stem + ".", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with open(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        bak = target.with_suffix(target.suffix + ".bak")
        if target.exists():
            bak.write_text(target.read_text(encoding="utf-8"), encoding="utf-8")
        tmp_path.replace(target)
    finally:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


def save_primitive(
    rel_file: str,
    doc: dict[str, Any],
    *,
    enable_write: bool = False,
    confirm_unchanged_version: bool = False,
    board: str | None = None,
    primitives: Path | None = None,
    fixtures: Path | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    if not enable_write:
        raise TemplateWebError("save disabled; set enable_write=true", status=403, code="read_only")

    doc = {k: v for k, v in doc.items() if not str(k).startswith("_")}
    validate_primitive_schema(doc, primitives=primitives)
    target = resolve_primitive_save_path(rel_file, primitives=primitives)

    existing: dict[str, Any] | None = None
    if target.exists():
        existing = json.loads(target.read_text(encoding="utf-8"))
    assert_version_bumped(existing, doc, confirm_unchanged_version=confirm_unchanged_version)

    replay = None
    if board:
        replay = match_primitive_on_board(doc, board, fixtures=fixtures, db_path=db_path)

    atomic_write_json(target, doc)

    pytest_cmd = f"{_REPO_ROOT.as_posix()}/.venv/Scripts/python.exe -m pytest -q"
    return {
        "saved": target.relative_to(primitives or _PRIMITIVES_DIR).as_posix(),
        "schema_ok": True,
        "replay": replay,
        "pytest_command": pytest_cmd,
        "index_hint": "POST /api/index/rebuild to refresh __index_primitives.md",
    }


def round_trip_semantic_equal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return _strip_version(deepcopy(a)) == _strip_version(deepcopy(b))


def rebuild_primitive_index(*, repo: Path | None = None) -> dict[str, str]:
    root = repo or _REPO_ROOT
    py = root / ".venv" / "Scripts" / "python.exe"
    if not py.exists():
        py = Path(sys.executable)
    script = root / "scripts" / "build_primitive_index.py"
    proc = subprocess.run(
        [str(py), str(script)],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if proc.returncode != 0:
        raise TemplateWebError(
            proc.stderr or proc.stdout or "index rebuild failed", status=500
        )
    return {"status": "ok", "stdout": proc.stdout.strip()}