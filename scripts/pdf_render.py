"""PDF page rendering / text search — runs inside the external Camelot venv.

This script is **never imported** by the main circuit-graph venv (which has no
PDF dependency).  It is executed as a subprocess by
``scripts/datasheet_compare_lib.py`` using the interpreter of
``CAMELOT_VENV`` (see ``circuit_graph.config.camelot_venv``), which provides
``fitz`` (PyMuPDF).

Usage::

    python pdf_render.py probe  --pdf PATH
    python pdf_render.py render --pdf PATH --page N --zoom 2 --out PNG
    python pdf_render.py search --pdf PATH --page N --text TEXT

Exit codes: 0 = ok, 2 = fitz missing, 3 = page out of range, 1 = other error.
Failures print the reason on stderr; stdout stays pure JSON / nothing.
"""

from __future__ import annotations

import argparse
import json
import sys

# Chinese keywords for the "application circuit" page (source encoding UTF-8).
APP_CIRCUIT_ZH = "应用电路"
APP_CIRCUIT_EN = "typical application"


def _fail(message: str, code: int = 1) -> "None":
    sys.stderr.write(message.strip() + "\n")
    raise SystemExit(code)


def _load_fitz():
    try:
        import fitz  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on external venv
        _fail(f"fitz not available: {exc}", 2)
    return fitz


def _open_doc(fitz, pdf_path: str):
    try:
        return fitz.open(pdf_path)
    except Exception as exc:  # pragma: no cover - depends on the PDF file
        _fail(f"cannot open pdf: {exc}", 1)


def cmd_probe(args: argparse.Namespace) -> int:
    """Report page count and the default "application circuit" page."""
    fitz = _load_fitz()
    doc = _open_doc(fitz, args.pdf)
    pages = doc.page_count
    default_page = 1
    # First pass: English marker.
    for index in range(pages):
        text = doc[index].get_text().lower()
        if APP_CIRCUIT_EN in text:
            default_page = index + 1
            break
    else:
        # Second pass: Chinese marker.
        for index in range(pages):
            text = doc[index].get_text()
            if APP_CIRCUIT_ZH in text:
                default_page = index + 1
                break
    sys.stdout.write(json.dumps({"pages": pages, "default_page": default_page}))
    sys.stdout.write("\n")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    """Render one page to a PNG file."""
    fitz = _load_fitz()
    doc = _open_doc(fitz, args.pdf)
    if args.page < 1 or args.page > doc.page_count:
        _fail(f"page {args.page} out of range (1..{doc.page_count})", 3)
    page = doc[args.page - 1]
    matrix = fitz.Matrix(args.zoom, args.zoom)
    pix = page.get_pixmap(matrix=matrix)
    try:
        pix.save(args.out)
    except Exception as exc:  # pragma: no cover
        _fail(f"cannot write png: {exc}", 1)
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    """Locate *text* on one page; rectangles are normalised to 0..1 ratios."""
    fitz = _load_fitz()
    doc = _open_doc(fitz, args.pdf)
    if args.page < 1 or args.page > doc.page_count:
        _fail(f"page {args.page} out of range (1..{doc.page_count})", 3)
    page = doc[args.page - 1]
    try:
        found = page.search_for(args.text)
    except Exception as exc:  # pragma: no cover
        _fail(f"search failed: {exc}", 1)
    width = float(page.rect.width) or 1.0
    height = float(page.rect.height) or 1.0
    rects = [
        {
            "x": r.x0 / width,
            "y": r.y0 / height,
            "w": (r.x1 - r.x0) / width,
            "h": (r.y1 - r.y0) / height,
        }
        for r in found
    ]
    sys.stdout.write(json.dumps({"rects": rects, "page_size": [width, height]}))
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PDF render / search helper")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_probe = sub.add_parser("probe", help="page count + default page")
    p_probe.add_argument("--pdf", required=True)
    p_probe.set_defaults(func=cmd_probe)

    p_render = sub.add_parser("render", help="render a page to PNG")
    p_render.add_argument("--pdf", required=True)
    p_render.add_argument("--page", type=int, required=True)
    p_render.add_argument("--zoom", type=float, default=2.0)
    p_render.add_argument("--out", required=True)
    p_render.set_defaults(func=cmd_render)

    p_search = sub.add_parser("search", help="search text on a page")
    p_search.add_argument("--pdf", required=True)
    p_search.add_argument("--page", type=int, required=True)
    p_search.add_argument("--text", required=True)
    p_search.set_defaults(func=cmd_search)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
