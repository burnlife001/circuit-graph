"""Datasheet ↔ JSON topology comparison — pure logic behind the WebUI tab.

No PDF dependency lives in the main venv: page rendering and text search are
delegated to ``scripts/pdf_render.py``, executed with the interpreter of the
external ``CAMELOT_VENV`` (which provides ``fitz``/PyMuPDF), exactly like
``circuit_graph.knowledge.pdf_pin_extract`` does for Camelot.

Public API
----------
``locate_datasheet(board, chip)``
    Find the datasheet PDF of the chip's part number; report page count and
    the default "typical application" page.
``render_page(board, chip, page, zoom=2)``
    PNG bytes of one datasheet page (cached under ``.cg-cache/pdf-pages/``).
``search_page(board, chip, page, text)``
    Locate a text on a page; rectangles are normalised to 0..1 ratios.
``compare_graph(board, chip)``
    Per-pin topology of the chip (net + same-net members) enriched with the
    KB pin names.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

_SCRIPTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPTS_DIR.parent

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from template_web_lib import (  # noqa: E402
    TemplateWebError,
    _conn_section,
    _parse_board_ir,
    resolve_board_path,
    validate_board_name,
)

_PDF_RENDER = _SCRIPTS_DIR / "pdf_render.py"
_CACHE_DIR = _REPO_ROOT / ".cg-cache" / "pdf-pages"

MAX_SEARCH_LEN = 32
ALLOWED_ZOOMS = (1, 2, 3)
MEMBER_CAP = 8

# probe() results are cached per (pdf path, mtime) — page count of a datasheet
# never changes during a session and probing means opening the PDF.
_PROBE_CACHE: dict[tuple[str, int], dict[str, int]] = {}


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------


def validate_chip(chip: str) -> str:
    """Validate a component designator (no path traversal)."""
    chip = (chip or "").strip()
    if not chip or ".." in chip or "/" in chip or "\\" in chip:
        raise TemplateWebError("invalid chip", status=400, code="invalid_part")
    return chip


def validate_page(page: Any) -> int:
    """Validate a 1-based page number."""
    try:
        value = int(str(page).strip())
    except (TypeError, ValueError):
        raise TemplateWebError("invalid page", status=400, code="invalid_page") from None
    if value < 1:
        raise TemplateWebError("invalid page", status=400, code="invalid_page")
    return value


def validate_zoom(zoom: Any) -> int:
    """Validate the zoom factor (1x / 2x / 3x)."""
    try:
        value = int(str(zoom).strip())
    except (TypeError, ValueError):
        raise TemplateWebError("invalid zoom", status=400, code="invalid_zoom") from None
    if value not in ALLOWED_ZOOMS:
        raise TemplateWebError("invalid zoom", status=400, code="invalid_zoom")
    return value


def validate_search_text(text: str) -> str:
    """Validate the search term (non-empty, at most 32 characters)."""
    text = (text or "").strip()
    if not text or len(text) > MAX_SEARCH_LEN:
        raise TemplateWebError(
            "search text must be 1..%d characters" % MAX_SEARCH_LEN,
            status=400,
            code="invalid_text",
        )
    return text


# ---------------------------------------------------------------------------
# Board / chip resolution
# ---------------------------------------------------------------------------


def _chip_component(board: str, chip: str, *, fixtures=None, db_path=None):
    """Return the IR component of *chip* on *board*."""
    path = resolve_board_path(board, fixtures=fixtures)
    ir = _parse_board_ir(path, db_path=db_path)

    from circuit_graph.cli.query import _canonical

    comp_map = {c.id: c for c in ir.components}
    comp = comp_map.get(_canonical(ir, chip))
    if comp is None:
        raise TemplateWebError(
            f"component not found: {chip}", status=404, code="component_not_found"
        )
    return ir, comp


def _part_number(comp) -> str:
    from circuit_graph.generators.cg_graph import _format_component_value

    return comp.raw_value or _format_component_value(comp) or ""


def _datasheet_pdf(mpn: str):
    """Locate the datasheet PDF for *mpn* (``None`` when not found)."""
    if not mpn:
        return None
    from circuit_graph import config

    config.load()
    ds_dir = config.datasheet_dir()
    if ds_dir is None:
        return None
    from circuit_graph.knowledge.pdf_pin_extract import _find_datasheet

    return _find_datasheet(mpn, ds_dir)


def _ext_python() -> Path | None:
    """Interpreter of the external venv (``None`` when unavailable)."""
    from circuit_graph import config

    config.load()
    venv = config.camelot_venv()
    if venv is None:
        return None
    for name in ("python.exe", "python"):
        candidate = venv / "Scripts" / name
        if candidate.exists():
            return candidate
    return None


def _run_render(args: list[str], *, timeout: int = 90):
    """Run ``pdf_render.py`` in the external venv; returns the CompletedProcess."""
    py = _ext_python()
    if py is None:
        raise TemplateWebError(
            "renderer unavailable (external venv missing)",
            status=503,
            code="renderer_unavailable",
        )
    return subprocess.run(
        [str(py), str(_PDF_RENDER), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def _probe_pdf(pdf: Path) -> dict[str, int] | None:
    """Page count + default page for *pdf* (``None`` when the renderer fails)."""
    key = (str(pdf), pdf.stat().st_mtime_ns)
    cached = _PROBE_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        proc = _run_render(["probe", "--pdf", str(pdf)], timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        data = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "pages" not in data:
        return None
    result = {
        "pages": int(data.get("pages") or 0),
        "default_page": int(data.get("default_page") or 1),
    }
    _PROBE_CACHE[key] = result
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def locate_datasheet(
    board: str,
    chip: str,
    *,
    fixtures=None,
    db_path=None,
) -> dict[str, Any]:
    """Locate the datasheet of *chip* on *board*.

    Returns ``{mpn, found, pdf_name, pages, default_page, renderer}``.
    ``found`` is ``False`` (never an error) when no PDF matches the part
    number; ``renderer`` is ``"unavailable"`` when the external venv or
    ``fitz`` is missing.
    """
    validate_board_name(board)
    chip = validate_chip(chip)
    _, comp = _chip_component(board, chip, fixtures=fixtures, db_path=db_path)
    mpn = _part_number(comp)

    result: dict[str, Any] = {
        "mpn": mpn,
        "found": False,
        "pdf_name": None,
        "pages": 0,
        "default_page": 1,
        "renderer": "unavailable",
    }

    pdf = _datasheet_pdf(mpn)
    if pdf is None:
        return result

    result["found"] = True
    result["pdf_name"] = pdf.name
    probe = _probe_pdf(pdf)
    if probe is not None:
        result["pages"] = probe["pages"]
        result["default_page"] = probe["default_page"]
        result["renderer"] = "ok"
    return result


def _require_pdf(board: str, chip: str, *, fixtures=None, db_path=None):
    validate_board_name(board)
    chip = validate_chip(chip)
    _, comp = _chip_component(board, chip, fixtures=fixtures, db_path=db_path)
    mpn = _part_number(comp)
    pdf = _datasheet_pdf(mpn)
    if pdf is None:
        raise TemplateWebError(
            f"no datasheet found for {mpn or chip}",
            status=404,
            code="datasheet_not_found",
        )
    return pdf


def render_page(
    board: str,
    chip: str,
    page: Any,
    zoom: Any = 2,
    *,
    fixtures=None,
    db_path=None,
) -> bytes:
    """Render one datasheet page and return the PNG bytes."""
    page_no = validate_page(page)
    zoom_no = validate_zoom(zoom)
    pdf = _require_pdf(board, chip, fixtures=fixtures, db_path=db_path)

    stat = pdf.stat()
    key = hashlib.sha1(
        f"{pdf.resolve()}|{stat.st_mtime_ns}|{page_no}|{zoom_no}".encode("utf-8")
    ).hexdigest()
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached = _CACHE_DIR / f"{key}.png"
    if cached.exists() and cached.stat().st_size > 0:
        return cached.read_bytes()

    proc = _run_render(
        [
            "render",
            "--pdf",
            str(pdf),
            "--page",
            str(page_no),
            "--zoom",
            str(zoom_no),
            "--out",
            str(cached),
        ]
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if proc.returncode == 2:
            raise TemplateWebError(
                f"renderer unavailable: {stderr}",
                status=503,
                code="renderer_unavailable",
            )
        if proc.returncode == 3:
            raise TemplateWebError(
                stderr or f"page {page_no} out of range",
                status=400,
                code="page_out_of_range",
            )
        raise TemplateWebError(
            stderr or "pdf render failed", status=500, code="render_failed"
        )
    if not cached.exists():
        raise TemplateWebError("pdf render produced no output", status=500)
    return cached.read_bytes()


def search_page(
    board: str,
    chip: str,
    page: Any,
    text: str,
    *,
    fixtures=None,
    db_path=None,
) -> dict[str, Any]:
    """Search *text* on a datasheet page; rectangles are 0..1 ratios."""
    page_no = validate_page(page)
    query = validate_search_text(text)
    pdf = _require_pdf(board, chip, fixtures=fixtures, db_path=db_path)

    proc = _run_render(
        ["search", "--pdf", str(pdf), "--page", str(page_no), "--text", query]
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        if proc.returncode == 2:
            raise TemplateWebError(
                f"renderer unavailable: {stderr}",
                status=503,
                code="renderer_unavailable",
            )
        if proc.returncode == 3:
            raise TemplateWebError(
                stderr or f"page {page_no} out of range",
                status=400,
                code="page_out_of_range",
            )
        raise TemplateWebError(
            stderr or "pdf search failed", status=500, code="search_failed"
        )
    try:
        data = json.loads(proc.stdout.strip() or "{}")
    except json.JSONDecodeError as exc:
        raise TemplateWebError("pdf search returned invalid JSON", status=500) from exc
    rects = data.get("rects") or []
    page_size = data.get("page_size") or [0.0, 0.0]
    return {"rects": rects, "page_size": page_size}


def compare_graph(
    board: str,
    chip: str,
    *,
    db=None,
    fixtures=None,
    db_path=None,
) -> dict[str, Any]:
    """Per-pin topology of *chip*, with KB pin names when available."""
    validate_board_name(board)
    chip = validate_chip(chip)
    ir, comp = _chip_component(board, chip, fixtures=fixtures, db_path=db_path)
    conn = _conn_section(ir, chip)
    mpn = _part_number(comp)

    pinout_map: dict[str, str] = {}
    if mpn:
        try:
            if db is None:
                from circuit_graph.knowledge.db import ComponentDatabase

                db = ComponentDatabase.default()
            pinout = db.get_pinout(mpn)
        except Exception:  # pragma: no cover - KB is optional
            pinout = None
        if pinout:
            pinout_map = {str(k): str(v) for k, v in pinout.items()}

    pins = []
    for row in conn["pins"]:
        number = str(row.get("pin"))
        name = pinout_map.get(number) or row.get("name") or number
        members = list(row.get("members") or [])[:MEMBER_CAP]
        truncated = int(row.get("truncated") or 0) + max(
            0, len(row.get("members") or []) - MEMBER_CAP
        )
        pins.append(
            {
                "pin": number,
                "name": name,
                "net": row.get("net"),
                "members": members,
                "truncated": truncated,
            }
        )

    return {
        "chip": {
            "id": comp.id,
            "type": comp.device_kind.value.lower(),
            "value": conn["component"].get("value") or mpn,
            "part_number": mpn,
        },
        "pins": pins,
    }


__all__ = [
    "locate_datasheet",
    "render_page",
    "search_page",
    "compare_graph",
    "validate_chip",
    "validate_page",
    "validate_zoom",
    "validate_search_text",
]
