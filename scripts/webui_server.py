"""Unified Web UI server — KB editor + template layer editor in one process.

Single port (default 8766, bound to 127.0.0.1) serves three pages:

- ``/``    — tiny entry page linking to both editors
- ``/kb``  — KB knowledge-base editor (``scripts/kb_demo.html``)
- ``/tpl`` — template layer editor (``scripts/template_demo.html``)

API routing is delegated to the two existing handlers — no route logic is
copied here:

- ``/api/components*``  → :class:`kb_server.KBAPIHandler`
- everything else under ``/api/`` → :class:`template_server.TemplateAPIHandler`

Usage::

    .venv/Scripts/python.exe scripts/webui_server.py [--port 8766] [--db PATH]
"""

from __future__ import annotations

import sys
from http.server import HTTPServer
from pathlib import Path
from urllib.parse import urlparse

_SCRIPTS_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPTS_DIR.parent

if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from kb_server import KBAPIHandler  # noqa: E402
from template_server import TemplateAPIHandler  # noqa: E402

KB_HTML = _SCRIPTS_DIR / "kb_demo.html"
TPL_HTML = _SCRIPTS_DIR / "template_demo.html"

INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Circuit-Graph WebUI</title>
<style>
  :root { color-scheme: dark; }
  body { font-family: system-ui, sans-serif; margin: 0; padding: 2rem;
         background: #0d0d0d; color: #c8c8c8; }
  h1 { font-size: 1.4rem; color: #e8e8e8; }
  .cards { display: flex; gap: 1rem; flex-wrap: wrap; margin-top: 1.5rem; }
  a.card { display: block; padding: 1rem 1.5rem; background: #1a1a1a;
           border: 1px solid #2a2a2a; border-radius: 8px; text-decoration: none;
           color: #93c5fd; font-weight: 600; }
  a.card:hover { background: #252525; border-color: #3b82f6; }
  a.card span { display: block; font-weight: 400; color: #6a6a6a; font-size: .85rem;
                margin-top: .25rem; }
</style>
</head>
<body>
<h1>Circuit-Graph WebUI</h1>
<p>知识库与模板层编辑器，统一入口。</p>
<div class="cards">
  <a class="card" href="/kb">KB 知识库<span>/kb</span></a>
  <a class="card" href="/tpl">模板层工作台<span>/tpl</span></a>
</div>
</body>
</html>
"""


class WebUIHandler(KBAPIHandler):
    """One handler that dispatches to the KB or the template layer routes."""

    # Inherited helpers (``_json``, ``_read_body``, ``db``) come from
    # ``KBAPIHandler``; the template handler's ``do_*`` methods reuse them.

    def _cors_headers(self) -> None:
        """No CORS headers — same-origin only (KB default would add them)."""

    # ------------------------------------------------------------------ GET

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._send_bytes(INDEX_HTML.encode("utf-8"))
        elif path == "/kb":
            self._send_file(KB_HTML)
        elif path == "/tpl":
            self._send_file(TPL_HTML)
        elif path.startswith("/api/components"):
            KBAPIHandler.do_GET(self)
        else:
            TemplateAPIHandler.do_GET(self)

    # ----------------------------------------------------------------- POST

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/components"):
            KBAPIHandler.do_POST(self)
        else:
            TemplateAPIHandler.do_POST(self)

    # ------------------------------------------------------------ PUT/DELETE

    def do_PUT(self) -> None:
        if urlparse(self.path).path.startswith("/api/components"):
            KBAPIHandler.do_PUT(self)
        else:
            self._json({"error": "not found"}, 404)

    def do_DELETE(self) -> None:
        if urlparse(self.path).path.startswith("/api/components"):
            KBAPIHandler.do_DELETE(self)
        else:
            self._json({"error": "not found"}, 404)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.end_headers()

    # ---------------------------------------------------------------- helpers

    def _send_file(self, path: Path) -> None:
        if not path.exists():
            self._json({"error": f"missing {path}"}, 500)
            return
        self._send_bytes(path.read_bytes())

    def _send_bytes(self, body: bytes) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # --------------------------------------------------------------- logging

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[webui] {fmt % args}", file=sys.stderr)


def main() -> None:
    import argparse

    from circuit_graph.knowledge.db import ComponentDatabase

    ap = argparse.ArgumentParser(description="Unified Web UI (KB + template layer)")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--db", type=Path, default=None, help="KB SQLite path")
    args = ap.parse_args()

    db = (
        ComponentDatabase.open(args.db)
        if args.db
        else ComponentDatabase.default()
    )
    KBAPIHandler.db = db
    TemplateAPIHandler.db = db

    addr = ("127.0.0.1", args.port)
    server = HTTPServer(addr, WebUIHandler)
    print(f"Circuit-Graph WebUI → http://127.0.0.1:{args.port}/")
    print("  /kb  知识库编辑器    /tpl  模板层工作台")
    print(f"DB: {db.db_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
