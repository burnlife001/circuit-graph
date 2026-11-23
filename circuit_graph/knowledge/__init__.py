"""Component part-number knowledge base.

:class:`ComponentDatabase` is the SQLite-backed store.  Use
``query(part_number=...)`` / ``query(category=...)`` to find components,
``get_pinout`` for pins, ``import_kicad_netlist`` /
``import_protel_netlist`` to ingest data, and ``resolve_pins`` to call the
chip resolver against missing entries.

Typical usage::

    from circuit_graph.knowledge import ComponentDatabase

    db = ComponentDatabase.default()
    db.init_db()                                       # idempotent
    db.import_kicad_netlist("netlist.net")             # bulk ingest
    hits = db.query(category="dcdc")                   # generic query
    pinout = db.get_pinout("JW5065")                   # {pin: name, ...}
"""

from __future__ import annotations

from circuit_graph.knowledge.db import (
    ComponentDatabase,
    ComponentRecord,
)

__all__ = ["ComponentDatabase", "ComponentRecord"]
