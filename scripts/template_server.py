"""Template layer Web UI — mode-layer editor (stdlib http.server, port 8766).

Phase 0 决策：独立于 kb_server（8765），本服务 8766 / 127.0.0.1；字段映射与值类型词表见
``todolist.md`` §3 与 ``template_web_lib.VALUE_TYPE_PATTERNS``。

Usage::

    .venv/Scripts/python.exe scripts/template_server.py [--port 8766]

Open http://127.0.0.1:8766/ — writes only under primitives/ when explicitly enabled.
"""

from __future__ import annotations

import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_SCRIPTS_DIR = Path(__file__).resolve().parent
_DEMO_HTML = _SCRIPTS_DIR / "template_demo.html"

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from template_web_lib import (  # noqa: E402
    TemplateWebError,
    build_render_model,
    generate_draft,
    list_board_components,
    list_boards,
    list_primitives_summary,
    load_primitive_raw,
    match_board,
    rebuild_primitive_index,
    save_primitive,
    validate_board_name,
    validate_primitive_id,
    validate_template_payload,
    board_kb_pinout,
    board_pin_neighbors,
)


class TemplateAPIHandler(BaseHTTPRequestHandler):
    db = None

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        qs = parse_qs(urlparse(self.path).query)

        try:
            if path == "/":
                self._serve_html()
            elif path == "/api/primitives":
                self._json(list_primitives_summary())
            elif path.startswith("/api/primitives/"):
                pid = path.rsplit("/", 1)[-1]
                validate_primitive_id(pid)
                raw = load_primitive_raw(pid)
                self._json({"raw": raw, "render": build_render_model(raw)})
            elif path == "/api/boards":
                self._json(list_boards())
            elif path.startswith("/api/boards/") and path.endswith("/components"):
                board = path.split("/")[3]
                validate_board_name(board)
                self._json(list_board_components(board))
            elif path.startswith("/api/boards/") and "/conn/" in path:
                parts = path.split("/")
                board, part = parts[3], parts[5]
                validate_board_name(board)
                self._json(board_pin_neighbors(board, part))
            elif path.startswith("/api/boards/") and "/pinout/" in path:
                parts = path.split("/")
                board, part = parts[3], parts[5]
                validate_board_name(board)
                self._json(board_kb_pinout(board, part, self.db))
            elif path == "/api/match":
                board = (qs.get("board") or [""])[0]
                template_id = (qs.get("template_id") or [None])[0]
                validate_board_name(board)
                if template_id:
                    validate_primitive_id(template_id)
                self._json(match_board(board, template_id=template_id))
            else:
                self._json({"error": "not found"}, 404)
        except TemplateWebError as exc:
            self._json({"error": str(exc), "code": exc.code}, exc.status)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            body = self._read_body()
            if path == "/api/draft":
                self._json(generate_draft(body))
            elif path == "/api/validate":
                doc = body.get("template") or body
                board = body.get("board")
                self._json(
                    validate_template_payload(doc, board=board),
                )
            elif path == "/api/save":
                doc = body.get("template") or body
                rel = body.get("file") or doc.get("_file")
                if not rel:
                    raise TemplateWebError("file path required (relative to primitives/)")
                result = save_primitive(
                    str(rel),
                    doc,
                    enable_write=bool(body.get("enable_write")),
                    confirm_unchanged_version=bool(body.get("confirm_unchanged_version")),
                    board=body.get("board"),
                )
                self._json(result)
            elif path == "/api/index/rebuild":
                self._json(rebuild_primitive_index())
            else:
                self._json({"error": "not found"}, 404)
        except TemplateWebError as exc:
            self._json({"error": str(exc), "code": exc.code}, exc.status)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def _json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self) -> None:
        if not _DEMO_HTML.exists():
            self._json({"error": f"missing {_DEMO_HTML}"}, 500)
            return
        body = _DEMO_HTML.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[template_server] {fmt % args}", file=sys.stderr)


def main() -> None:
    import argparse

    from circuit_graph.knowledge.db import ComponentDatabase

    ap = argparse.ArgumentParser(description="Template layer Web UI")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--db", type=Path, default=None, help="KB SQLite path")
    args = ap.parse_args()

    db = (
        ComponentDatabase.open(args.db)
        if args.db
        else ComponentDatabase.default()
    )
    TemplateAPIHandler.db = db

    addr = ("127.0.0.1", args.port)
    server = HTTPServer(addr, TemplateAPIHandler)
    print(f"Template WebUI → http://127.0.0.1:{args.port}")
    print("层：模式层 · 只读默认（保存需 enable_write）")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()