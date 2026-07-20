"""KB web UI server — browser-based CRUD for the SQLite component database.

Usage::

    uv run python scripts/kb_server.py [--port 8765]

Then open http://localhost:8765 in your browser.

Dependencies: Python stdlib only (http.server + json + sqlite3).
"""

from __future__ import annotations

import json
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_DEMO_HTML = _PROJECT_ROOT / "scripts" / "kb_demo.html"

# ---------------------------------------------------------------------------
# API handler
# ---------------------------------------------------------------------------


class KBAPIHandler(BaseHTTPRequestHandler):
    """Serve the demo HTML at ``/`` and a REST API under ``/api/``."""

    db = None  # Set by main() before serving.

    # ------------------------------------------------------------------ routing

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._serve_html()
        elif path == "/api/components":
            self._list_components()
        elif path.startswith("/api/components/"):
            self._get_component(path)
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        if self.path == "/api/components":
            self._create_component()
        elif self.path.startswith("/api/components/") and self.path.endswith(
            "/pins"
        ):
            self._add_pin(self.path)
        else:
            self._json({"error": "not found"}, 404)

    def do_PUT(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/components/"):
            self._update_component(path)
        else:
            self._json({"error": "not found"}, 404)

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/components/"):
            self._delete_component(path)
        else:
            self._json({"error": "not found"}, 404)

    def do_OPTIONS(self) -> None:
        self._cors_headers()
        self.send_response(204)
        self.end_headers()

    # ----------------------------------------------------------------- helpers

    def _cors_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,PUT,DELETE,OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers", "Content-Type"
        )

    def _read_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        return json.loads(self.rfile.read(length))

    def _json(self, data: object, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_html(self) -> None:
        if not _DEMO_HTML.exists():
            self._json({"error": f"demo HTML not found: {_DEMO_HTML}"}, 500)
            return
        body = _DEMO_HTML.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ------------------------------------------------------------- API methods

    def _list_components(self) -> None:
        try:
            rows = self.db.conn.execute(
                """
                SELECT id, part_number, manufacturer, category, subcategory,
                       package, pin_count, is_credible, pin_source
                FROM components
                WHERE pin_count IS NOT NULL
                ORDER BY part_number
                """
            ).fetchall()
            result = []
            for r in rows:
                result.append(
                    {
                        "id": r["id"],
                        "part_number": r["part_number"],
                        "manufacturer": r["manufacturer"],
                        "category": r["category"],
                        "subcategory": r["subcategory"],
                        "package": r["package"],
                        "pin_count": r["pin_count"],
                        "is_credible": bool(r["is_credible"])
                        if r["is_credible"] is not None
                        else None,
                        "pin_source": r["pin_source"],
                    }
                )
            self._json(result)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def _get_component(self, path: str) -> None:
        comp_id = path.rsplit("/", 1)[-1]
        try:
            row = self.db.conn.execute(
                "SELECT * FROM components WHERE id = ?", (comp_id,)
            ).fetchone()
            if row is None:
                self._json({"error": "not found"}, 404)
                return
            pins = self.db.conn.execute(
                """
                SELECT pin_number, pin_name, pin_type
                FROM pinouts WHERE component_id = ?
                GROUP BY pin_number
                ORDER BY CAST(pin_number AS INTEGER)
                """,
                (comp_id,),
            ).fetchall()
            props = self.db.conn.execute(
                "SELECT key, value FROM properties WHERE component_id = ?",
                (comp_id,),
            ).fetchall()
            self._json(
                {
                    "id": row["id"],
                    "part_number": row["part_number"],
                    "manufacturer": row["manufacturer"],
                    "category": row["category"],
                    "subcategory": row["subcategory"],
                    "description": row["description"],
                    "package": row["package"],
                    "pin_count": row["pin_count"],
                    "is_credible": bool(row["is_credible"])
                    if row["is_credible"] is not None
                    else None,
                    "pin_source": row["pin_source"],
                    "pins": {r["pin_number"]: r["pin_name"] for r in pins},
                    "pin_types": {r["pin_number"]: r["pin_type"] for r in pins},
                    "properties": {r["key"]: r["value"] for r in props},
                }
            )
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def _create_component(self) -> None:
        data = self._read_body()
        pn = (data.get("part_number") or "").strip()
        if not pn:
            self._json({"error": "part_number is required"}, 400)
            return
        try:
            comp_id = self.db._upsert_component(
                pn,
                manufacturer=data.get("manufacturer"),
                category=data.get("category"),
                subcategory=data.get("subcategory"),
                description=data.get("description"),
                package=data.get("package"),
                pin_count=data.get("pin_count") or 0,
                pin_source=data.get("pin_source") or "manual",
            )
            # Insert pins if provided.
            pins = data.get("pins") or {}
            if isinstance(pins, dict) and pins:
                with self.db.conn:
                    for pin_number, pin_name in pins.items():
                        self.db.conn.execute(
                            """
                            INSERT INTO pinouts (
                                component_id, pin_number, pin_name, pin_type, source_ref
                            ) VALUES (?, ?, ?, NULL, NULL)
                            ON CONFLICT(component_id, pin_number, source_ref) DO UPDATE SET
                                pin_name = excluded.pin_name
                            """,
                            (comp_id, str(pin_number), str(pin_name)),
                        )
            self.db._record_provenance(comp_id, "kb_webui", pn)
            self._json({"id": comp_id, "part_number": pn}, 201)
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def _update_component(self, path: str) -> None:
        comp_id = path.rsplit("/", 1)[-1]
        data = self._read_body()
        try:
            # Re-read existing row.
            row = self.db.conn.execute(
                "SELECT * FROM components WHERE id = ?", (comp_id,)
            ).fetchone()
            if row is None:
                self._json({"error": "not found"}, 404)
                return

            fields: dict[str, Any] = {}
            for col in (
                "part_number",
                "manufacturer",
                "category",
                "subcategory",
                "description",
                "package",
                "pin_count",
                "pin_source",
            ):
                if col in data:
                    fields[col] = data[col]

            is_credible_val = data.get("is_credible")
            if is_credible_val is not None:
                fields["is_credible"] = 1 if is_credible_val else 0

            if fields:
                sets = ", ".join(
                    f"{k} = ?" for k in fields
                )
                self.db.conn.execute(
                    f"UPDATE components SET {sets} WHERE id = ?",
                    tuple(fields.values()) + (comp_id,),
                )
                self.db.conn.commit()

            # Update pins if provided.
            pins = data.get("pins")
            if pins is not None and isinstance(pins, dict):
                with self.db.conn:
                    self.db.conn.execute(
                        "DELETE FROM pinouts WHERE component_id = ? AND source_ref IS NULL",
                        (comp_id,),
                    )
                    for pin_number, pin_name in pins.items():
                        if pin_name is not None and str(pin_name).strip():
                            self.db.conn.execute(
                                """
                                INSERT INTO pinouts (
                                    component_id, pin_number, pin_name, pin_type, source_ref
                                ) VALUES (?, ?, ?, NULL, NULL)
                                """,
                                (comp_id, str(pin_number), str(pin_name)),
                            )

            self._json({"ok": True, "id": int(comp_id)})
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def _delete_component(self, path: str) -> None:
        comp_id = path.rsplit("/", 1)[-1]
        try:
            with self.db.conn:
                self.db.conn.execute(
                    "DELETE FROM pinouts WHERE component_id = ?", (comp_id,)
                )
                self.db.conn.execute(
                    "DELETE FROM properties WHERE component_id = ?", (comp_id,)
                )
                self.db.conn.execute(
                    "DELETE FROM provenance WHERE component_id = ?", (comp_id,)
                )
                self.db.conn.execute(
                    "DELETE FROM components WHERE id = ?", (comp_id,)
                )
            self._json({"ok": True})
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    def _add_pin(self, path: str) -> None:
        comp_id = path.rsplit("/pins", 1)[0].rsplit("/", 1)[-1]
        data = self._read_body()
        pn = str(data.get("pin_number", "")).strip()
        name = str(data.get("pin_name", "")).strip()
        if not pn or not name:
            self._json({"error": "pin_number and pin_name required"}, 400)
            return
        try:
            with self.db.conn:
                self.db.conn.execute(
                    """
                    INSERT INTO pinouts (
                        component_id, pin_number, pin_name, pin_type, source_ref
                    ) VALUES (?, ?, ?, NULL, NULL)
                    ON CONFLICT(component_id, pin_number, source_ref) DO UPDATE SET
                        pin_name = excluded.pin_name
                    """,
                    (comp_id, pn, name),
                )
            self._json({"ok": True})
        except Exception as exc:
            self._json({"error": str(exc)}, 500)

    # --------------------------------------------------------------- logging

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[kb_server] {fmt % args}", file=sys.stderr)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    import argparse

    from circuit_graph.knowledge.db import ComponentDatabase

    ap = argparse.ArgumentParser(description="KB web UI server")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument(
        "--db",
        type=Path,
        default=None,
        help="Path to circuit_graph.db (default: bundled)",
    )
    args = ap.parse_args()

    db = (
        ComponentDatabase.open(args.db)
        if args.db
        else ComponentDatabase.default()
    )
    KBAPIHandler.db = db

    addr = ("127.0.0.1", args.port)
    server = HTTPServer(addr, KBAPIHandler)
    print(f"KB web UI → http://localhost:{args.port}")
    print(f"DB: {db.db_path}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
