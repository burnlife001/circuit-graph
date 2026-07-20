"""SQLite-backed component knowledge base.

This module replaces the legacy ``vendor_db.json`` + ``KnowledgeBase`` prefix
helper layer with a general-purpose SQLite store.  All knowledge about a part
— category, manufacturer, package, pinout, free-form properties, provenance —
lives in a single database file so adding a new data source or category never
requires changing the code.

Public API
----------

* :class:`ComponentRecord` — a typed view of one row in ``components``.
* :class:`ComponentDatabase` — owns the connection and exposes queries +
  importers.  Construct with :meth:`ComponentDatabase.open` or :meth:`default`.

Schema lives in :data:`SCHEMA_DDL`.  The four tables:

* ``components`` — one row per ``part_number`` (unique).  Holds category,
  manufacturer, package, pin_count, free-form description.
* ``pinouts`` — one row per (component, pin_number, source_ref).  The
  ``source_ref`` column records the board-level reference designator
  (e.g. ``U2``) so different boards using the same chip can keep separate
  pin-function maps.
* ``properties`` — extension KV table for arbitrary per-component metadata
  (output voltage, max current, package height, …).
* ``provenance`` — append-only audit trail recording where each row came from.

Design notes:

* SQLite is part of Python's stdlib → zero install cost.
* WAL journaling is enabled for concurrent read while importing.
* Every public mutator uses ``INSERT … ON CONFLICT … UPDATE`` so re-importing
  the same file is idempotent.
* All public methods accept ``Path | str`` for ergonomic CLI usage.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, NamedTuple, Optional

from circuit_graph.ir import DeviceKind
from circuit_graph.knowledge import chip_resolver as chip_resolver_mod
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser

_NON_PASSIVE_KINDS: frozenset[DeviceKind] = frozenset(
    {DeviceKind.IC, DeviceKind.MOSFET, DeviceKind.DIODE, DeviceKind.CONNECTOR}
)

_KICAD_PASSIVE_PREFIXES: frozenset[str] = frozenset({"R", "C", "L"})


def _is_passive_ref(ref: str) -> bool:
    """Return True if the KiCad designator is a passive component (R, C, L)."""
    m = re.match(r"^([A-Za-z]+)", ref)
    return m is not None and m.group(1).upper() in _KICAD_PASSIVE_PREFIXES


# ---------------------------------------------------------------------------
# Hardcoded pin maps for basic discrete components (bypass JLCPCB lookup)
# ---------------------------------------------------------------------------

_DISCRETE_PIN_MAPS: dict[tuple[str, int], dict[str, str]] = {
    ("diode", 2): {"1": "A", "2": "K"},
    ("bjt", 3): {"1": "B", "2": "E", "3": "C"},
    ("mosfet", 3): {"1": "G", "2": "S", "3": "D"},
}

# Heuristic part-number patterns for BJT vs MOSFET in SOT-23-3.
_BJT_PN_RE = re.compile(
    r"^(BC|MMBT|2N39|2N22|2SC|2SA|2SB|SS80|"
    r"8050|8550|901[2348]|"
    r"S8050|S8550|DTC|DTA|KTC|KTA|"
    r"2SD|2SJ|BF|ZTX|PMBT|PBSS)",
    re.IGNORECASE,
)

_DIODE_FP_PREFIXES: tuple[str, ...] = (
    "SOD-", "SOD", "SMA", "SMB", "SMC",
    "DO-", "DO2", "MICROMELF", "MINIMELF", "MELF",
    "DFN1006", "DFN0603",
    "SOT-23-2",  # 2-pin SOT-23: common for dual-diode or single
)

_SOT23_LIKE: tuple[str, ...] = ("SOT-23", "SOT23", "SOT-323", "SOT323",
                                 "NPN", "PNP", "9014")

_regulator_pn_re = re.compile(
    r"^(78L|78M|79L|79M|HT7[1-9]|HT75|HT71|LM1117|XC62|ME62|"
    r"RT9|RT8|AP2|AP1|LP29|LP39|TPS7|MIC5|SPX)",
    re.IGNORECASE,
)


def _classify_discrete(part_number: str, footprint: str | None,
                        pin_count: int) -> str | None:
    """Return 'diode', 'bjt', 'mosfet', or None for basic discretes."""
    if pin_count not in (2, 3):
        return None
    pn = (part_number or "").strip()
    fp = (footprint or "").upper()
    fp_clean = fp.replace(" ", "")

    # ---- 2-pin: diode ----
    if pin_count == 2:
        if any(fp_clean.startswith(p.upper().replace(" ", ""))
               for p in _DIODE_FP_PREFIXES):
            return "diode"
        # Part-number fallback for diodes (e.g. footprint may be missing/generic).
        pn_upper = pn.upper()
        for prefix in ("1N", "BAT", "BAS", "BZX", "MM3Z", "ES1", "ES2", "ES3",
                       "US1", "SS1", "SS2", "SS3", "M7", "M1", "M4",
                       "K34", "SMAJ", "P6KE", "SMBJ", "SMCJ"):
            if pn_upper.startswith(prefix):
                return "diode"
        return None

    # ---- 3-pin: BJT or MOSFET (must be SOT-23-like) ----
    if not any(kw in fp_clean for kw in _SOT23_LIKE):
        return None
    # Exclude regulators / non-transistor ICs.
    if _regulator_pn_re.match(pn):
        return None
    if _BJT_PN_RE.match(pn):
        return "bjt"
    # Default 3-pin SOT-23 to MOSFET (2N7002, AO3400, BSS138, etc.).
    return "mosfet"


def _apply_basic_pins(
    db: "ComponentDatabase",
    comp_id: int,
    part_number: str,
    footprint: str | None,
    pin_count: int,
    designator: str | None,
) -> bool:
    """Write hardcoded pins for basic discreets. Returns True if applied."""
    category = _classify_discrete(part_number, footprint, pin_count)
    if category is None:
        return False

    pin_map = _DISCRETE_PIN_MAPS.get((category, pin_count))
    if pin_map is None:
        return False

    db.conn.execute(
        "UPDATE components SET pin_source='local', is_credible=1 WHERE id=?",
        (comp_id,),
    )
    # Purge stale generic pins (old JLCPCB data with NULL source_ref)
    # so local hardcoded pins are authoritative.
    db.conn.execute(
        "DELETE FROM pinouts WHERE component_id=? AND source_ref IS NULL",
        (comp_id,),
    )
    for pin_number, pin_name in pin_map.items():
        db.conn.execute(
            """INSERT INTO pinouts (
                component_id, pin_number, pin_name, pin_type, source_ref
            ) VALUES (?, ?, ?, 'local', ?)
            ON CONFLICT(component_id, pin_number, source_ref) DO UPDATE SET
                pin_name = excluded.pin_name,
                pin_type = excluded.pin_type""",
            (comp_id, pin_number, pin_name, designator),
        )
    return True

# ---------------------------------------------------------------------------
# Schema

SCHEMA_DDL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS components (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        part_number TEXT NOT NULL UNIQUE,
        manufacturer TEXT,
        category TEXT,
        subcategory TEXT,
        description TEXT,
        package TEXT,
        pin_count INTEGER,
        pin_source TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_components_part_number ON components(part_number)",
    "CREATE INDEX IF NOT EXISTS idx_components_category ON components(category)",
    "CREATE INDEX IF NOT EXISTS idx_components_manufacturer ON components(manufacturer)",
    """
    CREATE TABLE IF NOT EXISTS pinouts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        component_id INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
        pin_number TEXT NOT NULL,
        pin_name TEXT NOT NULL,
        pin_type TEXT,
        direction TEXT,
        source_ref TEXT,
        UNIQUE(component_id, pin_number, source_ref)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pinouts_component_id ON pinouts(component_id)",
    """
    CREATE TABLE IF NOT EXISTS properties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        component_id INTEGER NOT NULL REFERENCES components(id) ON DELETE CASCADE,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        unit TEXT,
        UNIQUE(component_id, key)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_properties_component_id ON properties(component_id)",
    """
    CREATE TABLE IF NOT EXISTS provenance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        component_id INTEGER REFERENCES components(id) ON DELETE SET NULL,
        source TEXT NOT NULL,
        source_path TEXT,
        imported_at TEXT DEFAULT (datetime('now'))
    )
    """,
)


# ---------------------------------------------------------------------------
# Records


class ComponentRecord(NamedTuple):
    """One row from the ``components`` table.

    Use as a typed query result; mutate the database through
    :class:`ComponentDatabase` instead of touching the namedtuple directly.
    """

    id: int
    part_number: str
    manufacturer: Optional[str]
    category: Optional[str]
    subcategory: Optional[str]
    description: Optional[str]
    package: Optional[str]
    pin_count: Optional[int]
    pin_source: Optional[str]
    is_credible: Optional[bool]
    resolved_package: Optional[str] = None
    variant_of: Optional[int] = None
    channel_type: Optional[str] = None


# ---------------------------------------------------------------------------
# Database


_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "circuit_graph.db"


@dataclass
class ComponentDatabase:
    """SQLite-backed component knowledge base.

    Construct via :meth:`open` (path required) or :meth:`default` (uses the
    bundled ``circuit_graph.db`` next to this module).  The database file is
    created on first use; subsequent calls open it with WAL journaling.

    All write methods are idempotent — re-importing the same KiCad netlist or
    JSON file multiple times converges to the same database state.
    """

    db_path: Path
    _conn: Optional[sqlite3.Connection] = None
    pin_resolver_warnings: list[str] = field(default_factory=list)
    # When True, netlist import methods automatically run pin2json on ICs.
    auto_resolve_ics_on_import: bool = True
    # Optional datasheet directory for PDF-based pin extraction (Stage 5).
    datasheet_dir: Optional[Path] = None

    # -------------------------------------------------------------- factory

    @classmethod
    def open(
        cls, db_path: Path | str,
        *, datasheet_dir: Optional[Path] = None,
    ) -> "ComponentDatabase":
        """Open an existing database or create a fresh one at ``db_path``.

        *datasheet_dir* — optional path to a PDF datasheet directory for the
        Stage 5 pin-resolver fallback.  When omitted the stage is skipped.
        """
        instance = cls(Path(db_path), datasheet_dir=datasheet_dir)
        instance.init_db()
        return instance

    @classmethod
    def default(cls) -> "ComponentDatabase":
        """Open the default database next to this module.

        Reads ``DATASHEET_DIR`` from the environment (via :func:`config.datasheet_dir`)
        so PDF-based pin extraction is available when the env var is set.
        """
        from circuit_graph.config import datasheet_dir as _get_ds_dir
        return cls.open(_DEFAULT_DB_PATH, datasheet_dir=_get_ds_dir())

    # -------------------------------------------------------------- lifecycle

    def init_db(self) -> None:
        """Create the schema if it does not exist.

        Enables WAL journaling so multiple readers do not block a writer.
        Safe to call multiple times.
        """
        # ``check_same_thread=False`` lets us pass the connection into a
        # worker thread for batch resolves.  We never mutate from threads.
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._conn:
            for ddl in SCHEMA_DDL:
                self._conn.execute(ddl)
            # Migration: add legacy columns on older databases.
            self._migrate_add_column(
                "components", "is_credible",
                "ALTER TABLE components ADD COLUMN is_credible INTEGER DEFAULT NULL",
            )
            self._migrate_add_column(
                "components", "pin_source",
                "ALTER TABLE components ADD COLUMN pin_source TEXT DEFAULT NULL",
            )
            # Backfill: existing credible rows get pin_source='jlcpcb'.
            self._conn.execute(
                """
                UPDATE components SET pin_source = 'jlcpcb'
                WHERE is_credible = 1 AND pin_source IS NULL
                """
            )
            # Migration: chipdb pin_source no longer exists → manual.
            self._conn.execute(
                """
                UPDATE components SET pin_source = 'manual'
                WHERE pin_source = 'chipdb'
                """
            )
            # Migration: package-match column + self-referencing variant FK.
            self._migrate_add_column(
                "components", "resolved_package",
                "ALTER TABLE components ADD COLUMN resolved_package TEXT DEFAULT NULL",
            )
            self._migrate_add_column(
                "components", "variant_of",
                "ALTER TABLE components ADD COLUMN variant_of INTEGER "
                "DEFAULT NULL REFERENCES components(id) ON DELETE SET NULL",
            )
            # Migration: structured MOSFET/transistor channel type for disambiguation.
            self._migrate_channel_type()

    def _migrate_add_column(self, table: str, column: str, ddl: str) -> None:
        """Run *ddl* only if *table* does not already have *column*."""
        row = self._conn.execute(
            "PRAGMA table_info({})".format(table.replace('"', '""'))
        ).fetchall()
        existing = {r["name"] for r in row}
        if column not in existing:
            self._conn.execute(ddl)
            return True
        return False

    def _migrate_channel_type(self) -> None:
        """Add channel_type column and backfill PE2312."""
        added = self._migrate_add_column(
            "components", "channel_type",
            "ALTER TABLE components ADD COLUMN channel_type TEXT DEFAULT NULL",
        )
        if added:
            # Backfill: PE2312 is a complementary P+N MOSFET pair.
            self._conn.execute(
                """
                UPDATE components SET channel_type = 'complementary_pn'
                WHERE part_number = 'PE2312'
                """
            )

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("ComponentDatabase.init_db() was never called")
        return self._conn

    def close(self) -> None:
        """Close the underlying connection (mostly for tests)."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _derive_is_credible(pin_source: Optional[str]) -> Optional[int]:
        """Map ``pin_source`` to the legacy ``is_credible`` flag.

        ``jlcpcb``, ``kicad_official``, ``easyeda``, and ``datasheet``
        sources are credible.  ``manual`` and ``unknown`` are not.
        ``None`` means no source known.
        """
        if pin_source is None:
            return None
        return 1 if pin_source in ("jlcpcb", "kicad_official", "easyeda", "datasheet") else 0

    def _row_to_record(self, row: sqlite3.Row) -> ComponentRecord:
        ps = row["pin_source"] if "pin_source" in row.keys() else None
        if ps is not None:
            is_credible = bool(self._derive_is_credible(ps))
        else:
            raw = row["is_credible"]
            is_credible = bool(raw) if raw is not None else None

        keys = row.keys()
        resolved_pkg = row["resolved_package"] if "resolved_package" in keys else None
        variant_of = row["variant_of"] if "variant_of" in keys else None
        channel_type = row["channel_type"] if "channel_type" in keys else None

        return ComponentRecord(
            id=row["id"],
            part_number=row["part_number"],
            manufacturer=row["manufacturer"],
            category=row["category"],
            subcategory=row["subcategory"],
            description=row["description"],
            package=row["package"],
            pin_count=row["pin_count"],
            pin_source=ps,
            is_credible=is_credible,
            resolved_package=resolved_pkg,
            variant_of=variant_of,
            channel_type=channel_type,
        )

    def _upsert_component(
        self,
        part_number: str,
        *,
        manufacturer: Optional[str] = None,
        category: Optional[str] = None,
        subcategory: Optional[str] = None,
        description: Optional[str] = None,
        package: Optional[str] = None,
        pin_count: Optional[int] = None,
        is_credible: Optional[bool] = None,
        pin_source: Optional[str] = None,
        resolved_package: Optional[str] = None,
        variant_of: Optional[int] = None,
    ) -> int:
        """Insert or update a single component row, returning its id.

        When *pin_source* is given it takes precedence; ``is_credible`` is
        derived from it.  Otherwise the caller's ``is_credible`` value is
        used directly (legacy compatibility).

        ``resolved_package`` records the standard package name matched by
        :mod:`circuit_graph.knowledge.package_map` when JLCPCB returns a
        variant; it does NOT overwrite the board-level ``package`` string.
        ``variant_of`` is the FK id of a parent component this row is a
        variant of (used when board-level vs JLCPCB results diverge).
        """
        _is_credible = (
            self._derive_is_credible(pin_source)
            if pin_source is not None
            else (1 if is_credible else 0) if is_credible is not None
            else None
        )
        self.conn.execute(
            """
            INSERT INTO components (
                part_number, manufacturer, category, subcategory,
                description, package, pin_count, is_credible, pin_source,
                resolved_package, variant_of
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(part_number) DO UPDATE SET
                manufacturer     = COALESCE(excluded.manufacturer,     manufacturer),
                category         = COALESCE(excluded.category,         category),
                subcategory      = COALESCE(excluded.subcategory,      subcategory),
                description      = COALESCE(excluded.description,      description),
                package          = COALESCE(excluded.package,          package),
                pin_count        = COALESCE(excluded.pin_count,        pin_count),
                is_credible      = COALESCE(excluded.is_credible,      is_credible),
                pin_source       = COALESCE(excluded.pin_source,       pin_source),
                resolved_package = COALESCE(excluded.resolved_package, resolved_package),
                variant_of       = COALESCE(excluded.variant_of,       variant_of),
                updated_at       = datetime('now')
            """,
            (
                part_number,
                manufacturer,
                category,
                subcategory,
                description,
                package,
                pin_count,
                _is_credible,
                pin_source,
                resolved_package,
                variant_of,
            ),
        )
        row = self.conn.execute(
            "SELECT id FROM components WHERE part_number = ?",
            (part_number,),
        ).fetchone()
        assert row is not None
        return int(row["id"])

    def _record_provenance(
        self,
        component_id: int,
        source: str,
        source_path: Optional[str] = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO provenance (component_id, source, source_path)
            VALUES (?, ?, ?)
            """,
            (component_id, source, source_path),
        )

    # -------------------------------------------------------------- queries

    def query(self, **filters: Any) -> list[ComponentRecord]:
        """Generic filter on ``components``.

        Recognised keys:

        * ``part_number`` — exact match (case-insensitive).
        * ``part_number_like`` — LIKE glob (case-insensitive).
        * ``category``, ``manufacturer``, ``subcategory``, ``package`` — exact match.
        * ``any`` — free-text search across part_number / manufacturer /
          description / category via LIKE.

        Unknown filter keys are ignored silently so callers can pass looser
        filter bags without coordinating schema knowledge.
        """
        clauses: list[str] = []
        params: list[Any] = []
        mapping = {
            "part_number": ("part_number = ?", lambda v: v.upper()),
            "part_number_like": ("part_number LIKE ?", str),
            "category": ("category = ?", str),
            "manufacturer": ("manufacturer = ?", str),
            "subcategory": ("subcategory = ?", str),
            "package": ("package = ?", str),
            "variant_of": ("variant_of = ?", lambda v: int(v)),
        }
        for key, value in filters.items():
            if value is None:
                continue
            if key in mapping:
                sql, transform = mapping[key]
                clauses.append(sql)
                params.append(transform(value))
            elif key == "any":
                like = f"%{value}%"
                clauses.append(
                    "(part_number LIKE ? OR manufacturer LIKE ? "
                    "OR description LIKE ? OR category LIKE ?)"
                )
                params.extend([like, like, like, like])

        sql = "SELECT * FROM components"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY part_number"
        rows = self.conn.execute(sql, params).fetchall()
        return [self._row_to_record(r) for r in rows]

    def get_component(self, part_number: str) -> Optional[ComponentRecord]:
        """Look up a single component by ``part_number`` (case-insensitive)."""
        rows = self.query(part_number=part_number)
        return rows[0] if rows else None

    def lookup_prefix(self, part_number: str) -> Optional[ComponentRecord]:
        """Return the longest component row whose ``part_number`` prefixes ``part_number``.

        This replicates the legacy ``KnowledgeBase.lookup_prefix`` behaviour:
        ``JW5065`` matches a ``JW`` row (category ``dcdc``) when no longer or
        exact match exists.
        """
        if not part_number:
            return None
        row = self.conn.execute(
            """
            SELECT * FROM components
            WHERE UPPER(?) LIKE UPPER(part_number) || '%'
            ORDER BY LENGTH(part_number) DESC
            LIMIT 1
            """,
            (part_number,),
        ).fetchone()
        return self._row_to_record(row) if row else None

    def get_category(self, part_number: str) -> Optional[str]:
        """Return the category of the longest matching prefix, if any."""
        rec = self.lookup_prefix(part_number)
        return rec.category if rec else None

    def get_pinout(
        self,
        part_number: str,
        source_ref: Optional[str] = None,
    ) -> Optional[dict[str, str]]:
        """Return ``{pin_number: pin_name}`` for ``part_number``.

        ``source_ref`` selects a board-specific mapping when present; with
        ``None`` the generic (source_ref IS NULL) mapping is returned.  When
        the component has no entry at all, returns ``None``.
        """
        comp = self.get_component(part_number)
        if comp is None:
            return None
        if source_ref is None:
            rows = self.conn.execute(
                """
                SELECT pin_number, pin_name FROM pinouts
                WHERE component_id = ? AND source_ref IS NULL
                ORDER BY pin_number
                """,
                (comp.id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT pin_number, pin_name FROM pinouts
                WHERE component_id = ? AND source_ref = ?
                ORDER BY pin_number
                """,
                (comp.id, source_ref),
            ).fetchall()
        if not rows:
            # Fall back to *any* mapping when the requested scope is empty;
            # this lets a caller asking for a specific board still see generic
            # pin names when the board-specific map has not been recorded.
            rows = self.conn.execute(
                """
                SELECT pin_number, pin_name FROM pinouts
                WHERE component_id = ?
                ORDER BY pin_number
                """,
                (comp.id,),
            ).fetchall()
        if not rows:
            return None
        return {r["pin_number"]: r["pin_name"] for r in rows}

    def get_properties(self, part_number: str) -> dict[str, str]:
        """Return ``{key: value}`` for ``part_number`` (empty if none)."""
        comp = self.get_component(part_number)
        if comp is None:
            return {}
        rows = self.conn.execute(
            "SELECT key, value, unit FROM properties WHERE component_id = ? ORDER BY key",
            (comp.id,),
        ).fetchall()
        out: dict[str, str] = {}
        for r in rows:
            if r["unit"]:
                out[r["key"]] = f"{r['value']} {r['unit']}"
            else:
                out[r["key"]] = r["value"]
        return out

    def set_property(
        self,
        part_number: str,
        key: str,
        value: str,
        unit: Optional[str] = None,
    ) -> None:
        """Set or update a single KV property on ``part_number``."""
        comp_id = self._upsert_component(part_number)
        self.conn.execute(
            """
            INSERT INTO properties (component_id, key, value, unit)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(component_id, key) DO UPDATE SET
                value = excluded.value,
                unit  = excluded.unit
            """,
            (comp_id, key, value, unit),
        )
        self.conn.commit()

    # -------------------------------------------------------------- import

    def import_kicad_netlist(
        self, netlist_path: Path | str,
        *, symbol_dirs: list[Path | str] | None = None,
    ) -> int:
        """Parse a KiCad S-expression netlist and upsert everything into the KB.

        Walks every ``(comp ...)`` block, captures ``value``/``footprint``/
        ``description`` for the ``components`` table, then walks the matching
        ``(comp (ref ...) (pin ...) (pinfunction ...))`` blocks for pins.

        Returns the number of components imported (re-imports of the same
        part-number count too — the metric is "rows touched", not "new").
        """
        netlist_path = Path(netlist_path)
        with netlist_path.open("r", encoding="utf-8") as f:
            content = f.read()

        comps = _parse_kicad_netlist(content)
        if not comps:
            return 0

        count = 0
        ics: list[tuple[str, Optional[str]]] = []
        with self.conn:
            for comp in comps:
                # Skip passive components (R*, C*, L*) — same semantics as
                # _NON_PASSIVE_KINDS filtering in import_protel_netlist.
                ref = comp.get("ref", "")
                if ref and _is_passive_ref(ref):
                    continue
                # The KiCad ``value`` field IS the part number for ICs.
                part_number = comp.get("value")
                if not part_number:
                    continue
                comp_id = self._upsert_component(
                    part_number,
                    description=comp.get("description"),
                    package=comp.get("footprint"),
                    pin_count=len(comp.get("pins", {})),
                )
                for pin_number, pin_name in comp.get("pins", {}).items():
                    if not pin_name:
                        # KiCad emits ``(pin (num "N"))`` for every IC pin
                        # even when no symbol-library pinfunction is
                        # available; we only persist rows that carry a
                        # real name (filled by ``pin2json`` later).
                        continue
                    self.conn.execute(
                        """
                        INSERT INTO pinouts (
                            component_id, pin_number, pin_name, source_ref
                        ) VALUES (?, ?, ?, ?)
                        ON CONFLICT(component_id, pin_number, source_ref) DO UPDATE SET
                            pin_name = excluded.pin_name
                        """,
                        (comp_id, str(pin_number), pin_name, comp.get("ref")),
                    )
                self._record_provenance(
                    comp_id, "kicad_netlist", str(netlist_path)
                )
                count += 1
                # Try local hardcoded pin map first (diode / BJT / MOSFET).
                if _apply_basic_pins(
                    self, comp_id, part_number,
                    comp.get("footprint"), len(comp.get("pins", {})), comp.get("ref"),
                ):
                    continue  # locally resolved, skip JLCPCB
                # Mark as IC candidate — passive filtering already happened
                # upstream, so any remaining part_number is worth resolving.
                pn = part_number.strip()
                if pn and len(pn) >= 3 and any(c.isdigit() for c in pn):
                    ics.append((pn, comp.get("footprint")))
        # Resolve IC pins via the chip resolver (best-effort).
        self._auto_resolve_ics(ics, symbol_dirs=symbol_dirs)
        return count

    def import_protel_netlist(
        self, netlist_path: Path | str,
        *, symbol_dirs: list[Path | str] | None = None,
    ) -> int:
        """Parse Protel .NET netlist(s) and upsert everything into the KB.

        Accepts a single file or a directory.  When a directory is given, every
        ``.net`` / ``.NET`` file underneath it is imported in sorted order.

        For each component the part number is taken from ``component.part_number``
        when present (ICs), otherwise from ``component.raw_value`` so passive
        values are imported the same way KiCad ``value`` fields are.  Pins are
        recorded with ``source_ref`` set to the board-level designator so the
        same part number can carry different pin maps from different boards.

        Returns the number of components touched.
        """
        netlist_path = Path(netlist_path)
        total = 0
        ics: list[tuple[str, Optional[str]]] = []
        for path in _iter_net_files(netlist_path):
            content = _read_text_with_fallback(path)
            ir = ProtelNetParser().parse(
                ParserInput(content=content, source_id=str(path))
            )
            if not ir.components:
                continue
            with self.conn:
                for comp in ir.components:
                    if comp.device_kind not in _NON_PASSIVE_KINDS:
                        continue
                    part_number = comp.part_number or comp.raw_value
                    if not part_number:
                        continue
                    part_number = part_number.strip()
                    if not part_number:
                        continue
                    comp_id = self._upsert_component(
                        part_number,
                        package=comp.footprint,
                        pin_count=len(comp.pins),
                    )
                    for pin in comp.pins:
                        if pin.name is None:
                            continue  # skip unresolved pins; resolver fills later
                        self.conn.execute(
                            """
                            INSERT INTO pinouts (
                                component_id, pin_number, pin_name, source_ref
                            ) VALUES (?, ?, ?, ?)
                            ON CONFLICT(component_id, pin_number, source_ref) DO UPDATE SET
                                pin_name = excluded.pin_name
                            """,
                            (comp_id, pin.number, pin.name, comp.id),
                        )
                    self._record_provenance(
                        comp_id, "protel_netlist", str(path)
                    )
                    total += 1
                    if _apply_basic_pins(
                        self, comp_id, part_number, comp.footprint,
                        len(comp.pins), comp.id,
                    ):
                        continue  # locally resolved, skip JLCPCB
                    ics.append((part_number, comp.footprint))
        # Resolve IC pins via the chip resolver (best-effort).
        self._auto_resolve_ics(ics, symbol_dirs=symbol_dirs)
        return total

    # -------------------------------------------------------------- chip resolver

    def resolve_pins(
        self,
        components: Iterable[Any],
        symbol_dirs: list[Path | str],
    ) -> int:
        """Resolve unnamed pins for ``components`` via KB → pin2json.

        Walks each component in turn.  For every pin whose ``name`` is ``None``
        we try:

        1. KB lookup (read-only).
        2. ``pin2json`` against any matching ``.kicad_sym`` under ``symbol_dirs``.
        3. ``pin2json --lcsc`` when the part number matches ``C[0-9]+``.
        4. Leave ``pin.name`` alone on failure — never fabricate.

        Mutates ``component.pins`` in place.  Returns the count of pins
        successfully resolved.
        """
        symbol_dirs_paths = [Path(d) for d in symbol_dirs]
        resolved = 0
        self.pin_resolver_warnings = []
        for comp in components:
            if not comp.part_number:
                continue
            # Step 1 — KB lookup.  Use the part-number as-is first, then
            # the chip ``value`` as a fallback (Protel netlists often leave
            # part_number empty).
            pinout = self.get_pinout(comp.part_number)
            if pinout is None and getattr(comp, "value", None):
                pinout = self.get_pinout(comp.value)
            if pinout:
                resolved += self._fill_pin_names(comp, pinout)
                continue

            # Step 2 — symbol-dir search + pin2json.
            symbol = _find_kicad_symbols(comp.part_number, symbol_dirs_paths)
            if symbol is not None:
                payload = _invoke_pin2json(symbol, is_lcsc=False)
                if payload and payload.get("pins"):
                    self._upsert_from_pin2json(
                        comp.part_number, payload, pin_source="kicad_official"
                    )
                    resolved += self._fill_pin_names(comp, payload["pins"])
                    continue

            # Step 3 — LCSC fallback.
            if re.match(r"^[Cc]\d+$", comp.part_number or ""):
                payload = _invoke_pin2json(comp.part_number, is_lcsc=True)
                if payload and payload.get("pins"):
                    self._upsert_from_pin2json(comp.part_number, payload, pin_source="jlcpcb")
                    resolved += self._fill_pin_names(comp, payload["pins"])
                    continue

            self.pin_resolver_warnings.append(
                f"unresolved: {comp.id} ({comp.part_number})"
            )
        return resolved

    def _fill_pin_names(self, comp: Any, pinout: dict[str, str]) -> int:
        """Fill ``comp.pins[*].name`` from a ``{number: name}`` map; returns count."""
        filled = 0
        new_pins = []
        for pin in comp.pins:
            name = pin.name
            if name is None:
                name = pinout.get(str(pin.number)) or pinout.get(pin.number)
                if name:
                    filled += 1
            new_pins.append(
                _replace_pin(pin, name=name)
            )
        # mutate in place when supported (dataclass with frozen=False), else
        # assign back via object.__setattr__ for frozen dataclasses.
        try:
            comp.pins = new_pins  # type: ignore[attr-defined]
        except Exception:
            object.__setattr__(comp, "pins", new_pins)
        return filled

    def _upsert_from_pin2json(
        self,
        part_number: str,
        payload: dict[str, Any],
        *,
        pin_source: str = "jlcpcb",
        board_package: Optional[str] = None,
    ) -> Optional[int]:
        """Persist a chip-resolver result into the DB.

        *pin_source* records where the pin data came from — ``"jlcpcb"`` for
        JLCPCB symbol generation, ``"kicad_official"`` for locally-matched
        symbol files.

        *board_package*, when supplied, is the raw footprint string recorded
        by the importer (e.g. ``"SOT23-8"``).  It is passed through
        :mod:`circuit_graph.knowledge.package_map` to compute the canonical
        ``resolved_package`` but never overwrites the board-level ``package``
        column — the board footprint is the source of truth for physical
        geometry.  Returns the upserted component id, or ``None`` when the
        payload had no usable pin data.
        """
        pkg = payload.get("package")
        pins = payload.get("pins") or {}
        if not isinstance(pins, dict) or not pins:
            return None
        # Derive pin_type classification from the payload or compute inline.
        pin_types: dict[str, str] = payload.get("pin_types", {})
        if not pin_types and isinstance(pins, dict):
            try:
                pin_types = chip_resolver_mod.classify_pin_types(pins)
            except Exception:
                pin_types = {}
        # Resolve canonical package form without overwriting the board-level
        # ``package`` column.  Pass board_package so the canonical form is
        # grounded in the actual physical part.
        from circuit_graph.knowledge.package_map import match_package
        canonical_pkg = match_package(board_package or pkg)
        comp_id = self._upsert_component(
            part_number,
            # NOTE: do NOT overwrite ``package`` here — that column records
            # the raw board-level footprint and must stay authoritative.
            pin_count=len(pins) if isinstance(pins, dict) else None,
            pin_source=pin_source,
            resolved_package=canonical_pkg,
        )
        with self.conn:
            for pin_number, pin_name in pins.items():
                pt = pin_types.get(pin_number, "unknown") if pin_types else "unknown"
                self.conn.execute(
                    """
                    INSERT INTO pinouts (
                        component_id, pin_number, pin_name, pin_type, source_ref
                    ) VALUES (?, ?, ?, ?, NULL)
                    ON CONFLICT(component_id, pin_number, source_ref) DO UPDATE SET
                        pin_name = excluded.pin_name,
                        pin_type = excluded.pin_type
                    """,
                    (comp_id, str(pin_number), str(pin_name), pt),
                )
            self._record_provenance(comp_id, "pin2json", part_number)
        return comp_id

    def _auto_resolve_ics(
        self,
        part_numbers: list[tuple[str, Optional[str]]],
        symbol_dirs: list[Path | str] | None = None,
    ) -> int:
        """Resolve IC pin data via multi-stage fallback and persist to the KB.

        Resolution order for each IC (first success wins):

        1. **KB pre-check** — skip if any pin data already exists.
        2. **Local symbol-dir** — fuzzy-match ``.kicad_sym`` files, parse
           locally (``pin_source="kicad_official"``).
        3. **LCSC fallback** — for ``Cxxxxx`` part numbers resolve directly.
        4. **Batch JLCPCB** — remaining candidates in a single
           ``JLC2KiCadLib`` invocation (``pin_source="jlcpcb"``).

        Package-match policy (Stage 4 only):

        * When JLCPCB returns a package name that matches the board-level
          footprint per :func:`circuit_graph.knowledge.package_map.packages_match`,
          the pin map is written to the board component (``pin_source="jlcpcb"``).
        * When the package names do **not** match, the board component's
          ``pin_source`` is left at ``unknown`` and a fresh independent
          component row is created with the JLCPCB results, linked back to
          the board component via ``variant_of``.  This keeps the board
          pin map honest: a part whose footprint is ``QFN-26`` should not
          inherit pins from a ``QFN-26(4x4)`` JLCPCB symbol that happens to
          share a part number.

        Returns the count of newly resolved parts (board-level writes only;
        variant rows are counted as side-channel writes).
        """
        if not self.auto_resolve_ics_on_import:
            return 0

        # Import here to avoid a top-level cycle (package_map → db).
        from circuit_graph.knowledge.package_map import (
            match_package,
            packages_match,
        )

        sym_paths = [Path(d) for d in (symbol_dirs or [])]
        resolved = 0
        jl_candidates: list[tuple[str, Optional[str]]] = []

        for part_number, package in part_numbers:
            if not part_number:
                continue
            pn = part_number.strip()
            # Heuristic: skip obviously non-part-number values.
            # Passive filtering already happens upstream in both import
            # methods, so we only need length + digit guards here.
            if len(pn) < 3:
                continue
            if not any(c.isdigit() for c in pn):
                continue

            # Stage 1 — KB pre-check: skip if any pin data already exists.
            existing = self.get_component(pn)
            if existing is not None and (
                existing.pin_source is not None
                or self.get_pinout(pn) is not None
            ):
                continue

            # Stage 2 — local symbol-dir match.
            payload: Optional[dict[str, Any]] = None
            if sym_paths:
                sym = _find_kicad_symbols(pn, sym_paths)
                if sym is not None:
                    payload = _invoke_pin2json(sym, is_lcsc=False)
                    if payload and payload.get("pins"):
                        self._upsert_from_pin2json(
                            pn, payload, pin_source="kicad_official",
                            board_package=package,
                        )
                        resolved += 1
                        continue

            # Stage 3 — LCSC fallback for C-id part numbers.
            if re.match(r"^[Cc]\d+$", pn):
                payload = _invoke_pin2json(pn, is_lcsc=True)
                if payload and payload.get("pins"):
                    self._upsert_from_pin2json(
                        pn, payload, pin_source="jlcpcb",
                        board_package=package,
                    )
                    resolved += 1
                    continue

            # Queue for batch JLCPCB resolution.
            jl_candidates.append((pn, package))

        # Stage 4 — batch JLCPCB resolution with package-match gating.
        if jl_candidates:
            try:
                batch = chip_resolver_mod.resolve_parts_batch(jl_candidates)
            except Exception:
                batch = [None] * len(jl_candidates)
            for (pn, pkg), bres in zip(jl_candidates, batch):
                if bres is None or not bres.get("pins"):
                    continue
                jlc_pkg = bres.get("package")
                if packages_match(pkg, jlc_pkg):
                    # Match: write to the board-level component.
                    self._upsert_from_pin2json(
                        pn, bres, pin_source="jlcpcb", board_package=pkg,
                    )
                    resolved += 1
                else:
                    # Mismatch: keep board component's pin_source='unknown'
                    # and create an independent variant row.
                    board = self.get_component(pn)
                    variant_key = self._variant_key(pn, jlc_pkg)
                    # Skip if a variant row already exists for this part +
                    # jlc package — idempotent re-imports should converge.
                    existing_variant = self.query(part_number=variant_key)
                    if existing_variant:
                        continue
                    variant_id = self._upsert_component(
                        variant_key,
                        package=jlc_pkg,
                        pin_count=len(bres["pins"]),
                        pin_source="jlcpcb",
                        resolved_package=match_package(jlc_pkg),
                        variant_of=board.id if board else None,
                    )
                    with self.conn:
                        for pin_number, pin_name in bres["pins"].items():
                            pt = bres.get("pin_types", {}).get(
                                pin_number, "unknown",
                            )
                            self.conn.execute(
                                """
                                INSERT INTO pinouts (
                                    component_id, pin_number, pin_name,
                                    pin_type, source_ref
                                ) VALUES (?, ?, ?, ?, NULL)
                                ON CONFLICT(component_id, pin_number, source_ref)
                                DO UPDATE SET
                                    pin_name = excluded.pin_name,
                                    pin_type = excluded.pin_type
                                """,
                                (
                                    variant_id,
                                    str(pin_number),
                                    str(pin_name),
                                    pt,
                                ),
                            )
                        self._record_provenance(
                            variant_id, "pin2json_variant", pn,
                        )

        # Stage 5 — PDF datasheet fallback (兜底).
        #
        # Iterate over JLCPCB candidates that were not resolved; try to locate
        # a PDF datasheet and extract a pin table via Camelot.
        if self.datasheet_dir and jl_candidates:
            from circuit_graph.config import camelot_venv as _cv
            if _cv():
                from circuit_graph.knowledge.pdf_pin_extract import (
                    _find_datasheet,
                    extract_pin_table,
                )
                for pn, pkg in jl_candidates:
                    # Skip if already resolved by an earlier stage.
                    existing = self.get_component(pn)
                    if existing and existing.pin_source is not None:
                        continue
                    try:
                        pdf = _find_datasheet(pn, self.datasheet_dir)
                    except Exception:
                        continue
                    if pdf is None:
                        continue
                    try:
                        # Search for pin-description pages; fall back to
                        # the first page when none is found.
                        from circuit_graph.knowledge.pdf_pin_extract import (
                            search_pin_pages as _search_pages,
                        )
                        pages = _search_pages(pdf)
                        if not pages:
                            pages = [1]
                        pin_map = extract_pin_table(pdf, pages)
                    except Exception:
                        pin_map = None
                    if pin_map:
                        self._upsert_from_pin2json(
                            pn, {"pins": pin_map},
                            pin_source="datasheet",
                            board_package=pkg,
                        )
                        resolved += 1

        return resolved

    @staticmethod
    def _variant_key(part_number: str, jlc_package: Optional[str]) -> str:
        """Compose a stable variant-row key for a JLCPCB result.

        Format: ``"{part_number}@{jlc_package}"``.  Includes the resolved
        package so different variants of the same board part do not collide.
        """
        pkg_token = (jlc_package or "unknown").replace(" ", "_")
        return f"{part_number}@{pkg_token}"

    # -------------------------------------------------------------- connectors

    def import_connector_json(self, json_path: Path | str) -> int:
        """Load one connector schema JSON and upsert its pin map.

        Schema (see ``circuit_graph/knowledge/seed/connectors/*.json``):

        .. code-block:: json

            {
              "connector_type": "TYPE_C_16PIN",
              "package": "TYPE-C",
              "pin_count": 14,
              "pins": [
                {"number": "1", "name": "GND", "pin_type": "ground"},
                ...
              ]
            }

        The ``connector_type`` becomes the ``part_number``; ``package`` is
        stored in ``package`` and ``resolved_package``; pin names go into
        the ``pinouts`` table with ``pin_source = "manual"``.

        Returns the number of pins written.
        """
        json_path = Path(json_path)
        with json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        ctype = data["connector_type"]
        pins = data.get("pins") or []
        if not ctype or not pins:
            return 0

        pin_dict = {str(p["number"]): str(p["name"]) for p in pins}
        pin_types = {
            str(p["number"]): str(p.get("pin_type", "unknown"))
            for p in pins
        }
        pkg = data.get("package")

        comp_id = self._upsert_component(
            ctype,
            package=pkg,
            pin_count=len(pins),
            pin_source="manual",
            resolved_package=pkg,
        )
        with self.conn:
            for pin_no, pin_name in pin_dict.items():
                self.conn.execute(
                    """
                    INSERT INTO pinouts (component_id, pin_number, pin_name,
                                         pin_type, source_ref)
                    VALUES (?, ?, ?, ?, NULL)
                    ON CONFLICT(component_id, pin_number, source_ref)
                    DO UPDATE SET pin_name=excluded.pin_name,
                                  pin_type=excluded.pin_type
                    """,
                    (comp_id, pin_no, pin_name, pin_types.get(pin_no, "unknown")),
                )
            self._record_provenance(
                comp_id, "connector_json", str(json_path),
            )
        return len(pins)

    # -------------------------------------------------------------- stats

    def stats(self) -> dict[str, int]:
        """Return aggregate counts for ``cg kb stats``."""
        out: dict[str, int] = {}
        out["components"] = int(
            self.conn.execute("SELECT COUNT(*) AS n FROM components").fetchone()["n"]
        )
        out["pinouts"] = int(
            self.conn.execute("SELECT COUNT(*) AS n FROM pinouts").fetchone()["n"]
        )
        out["properties"] = int(
            self.conn.execute("SELECT COUNT(*) AS n FROM properties").fetchone()["n"]
        )
        out["provenance"] = int(
            self.conn.execute("SELECT COUNT(*) AS n FROM provenance").fetchone()["n"]
        )
        out["categories"] = int(
            self.conn.execute(
                "SELECT COUNT(DISTINCT category) AS n FROM components WHERE category IS NOT NULL"
            ).fetchone()["n"]
        )
        return out

    # -------------------------------------------------------------- migration

    def migrate_from_vendor_db(self, vendor_json_path: Path | str) -> int:
        """Migrate the legacy ``vendor_db.json`` prefix table into SQLite.

        Each prefix becomes its own component row whose ``part_number`` is the
        prefix itself and whose ``category`` is the legacy helper category.
        Idempotent — re-runs are no-ops on already-migrated prefixes.
        """
        vendor_json_path = Path(vendor_json_path)
        with vendor_json_path.open("r", encoding="utf-8") as f:
            data = json.load(f)

        entries = data.get("entries", {})
        if not entries:
            return 0

        count = 0
        with self.conn:
            for prefix, info in entries.items():
                if not isinstance(info, dict):
                    continue
                part_number = str(prefix).upper()
                comp_id = self._upsert_component(
                    part_number,
                    manufacturer=info.get("manufacturer"),
                    category=info.get("category"),
                    subcategory=info.get("subcategory"),
                    description=info.get("typical_topology"),
                )
                # Free-form fields go into the properties table so they
                # survive the JSON's shape changes.
                evidence = info.get("evidence")
                if isinstance(evidence, dict):
                    self.conn.execute(
                        """
                        INSERT INTO properties (component_id, key, value, unit)
                        VALUES (?, ?, ?, NULL)
                        ON CONFLICT(component_id, key) DO UPDATE SET
                            value = excluded.value
                        """,
                        (comp_id, "evidence_json", json.dumps(evidence)),
                    )
                self._record_provenance(
                    comp_id, "vendor_db_json", str(vendor_json_path)
                )
                count += 1
        return count


# ---------------------------------------------------------------------------
# Helpers (module-private; imported by tests and the PinNameResolver)


def _iter_net_files(path: Path) -> Iterator[Path]:
    """Yield ``.net``/``.NET`` files under ``path`` (or ``path`` itself if a file)."""
    if path.is_file():
        yield path
        return
    files = sorted(
        p for p in path.rglob("*") if p.is_file() and p.suffix.lower() in {".net"}
    )
    for p in files:
        yield p


def _read_text_with_fallback(path: Path) -> str:
    """Read a netlist file, tolerating legacy encodings (UTF-8 / GBK / Latin-1)."""
    raw = path.read_bytes()
    for encoding in ("utf-8", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _parse_kicad_netlist(content: str) -> list[dict[str, Any]]:
    """Parse a KiCad netlist and return a list of component dicts.

    Each dict has keys ``ref``, ``value``, ``footprint``, ``description``
    (all optional strings) and ``pins`` (``{pin_number: pin_name}``).
    """
    # Drop ``(...)`` comments so the brace counter doesn't get confused.
    content = re.sub(r";[^\n]*", "", content)

    out: list[dict[str, Any]] = []
    i = 0
    n = len(content)
    while i < n:
        # Look for the literal ``(comp`` introducer followed by whitespace —
        # this avoids matching the parent ``(components ...)`` wrapper.
        idx = content.find("(comp", i)
        if idx == -1:
            break
        # Reject ``(components`` (and any other ``(comp<word>`` opener).
        after = idx + 5  # length of "(comp"
        if after < n and content[after].isalpha():
            i = idx + 5
            continue
        end = _match_parens(content, idx)
        if end == -1:
            break
        block = content[idx:end]
        out.append(_parse_comp_block(block))
        i = end
    return out


def _match_parens(s: str, start: int) -> int:
    """Return the index just past the closing ``)`` for the ``(`` at ``start``.

    Returns ``-1`` when the parens never balance (malformed input).
    """
    assert s[start] == "("
    depth = 0
    in_str: Optional[str] = None
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if ch == "\\" and i + 1 < len(s):
                continue
            if ch == in_str:
                in_str = None
            continue
        if ch in {'"', "'"}:
            in_str = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
    return -1


def _parse_comp_block(block: str) -> dict[str, Any]:
    """Extract ref/value/footprint/description/pins from one ``(comp …)`` block.

    KiCad Eeschema (format "E") emits a single ``(comp ...)`` block per
    component with this shape (paraphrased)::

        (comp
          (ref "U2")
          (value "MM32F014X-QFP48")
          (footprint "LQFP-48")
          (libsource (lib "...") (part "...") (description "..."))
          (units
            (unit (name "A") (pins (pin (num "1")) (pin (num "2")) ...))
          )
        )

    We capture ``ref``, ``value``, ``footprint``, the libsource description,
    and every ``(pin (num "N"))`` nested under ``units``.  Pin function names
    are NOT in the netlist — they live in the symbol library — so each pin's
    ``name`` defaults to ``None``.  The pin2json pipeline back-fills them.
    """
    out: dict[str, Any] = {"pins": {}}
    out["ref"] = _extract_first_string(block, "ref")
    out["value"] = _extract_first_string(block, "value")
    out["footprint"] = _extract_first_string(block, "footprint")
    out["description"] = _extract_first_string(block, "description")
    # KiCad nests the description under ``libsource`` in newer exports; we
    # also check the ``(field (name "Description") ...)`` form.
    if not out["description"]:
        m = re.search(
            r'\(field\s+\(\s*name\s+"Description"\s*\)\s+"([^"]*)"',
            block,
        )
        if m:
            out["description"] = m.group(1)
        else:
            # Fallback: the libsource description.
            m = re.search(r"\(libsource\b[^)]*\(\s*description\s+\"([^\"]*)\"", block)
            if m:
                out["description"] = m.group(1)

    # Collect every ``(pin (num "N"))`` anywhere inside this comp block.  We
    # tolerate ``pin`` blocks with no ``num`` by skipping them.
    for pin_number in re.findall(
        r"\(pin\s+(?:\([^)]*\)\s*)*\(num\s+\"([^\"]*)\"", block
    ):
        # Name will be filled by pin2json / KB lookup; keep None here.
        out["pins"].setdefault(pin_number, None)
    return out


def _extract_first_string(block: str, key: str) -> Optional[str]:
    """Return the first quoted string argument for ``key`` in ``block``."""
    pattern = re.compile(rf"\(\s*{re.escape(key)}\s+\"([^\"]*)\"")
    m = pattern.search(block)
    return m.group(1) if m else None


def _find_kicad_symbols(
    part_number: str, search_dirs: list[Path]
) -> Optional[Path]:
    """Fuzzy-match ``.kicad_sym`` files for ``part_number`` under ``search_dirs``.

    Matching strategy (first hit wins):

    * Exact ``<part_number>.kicad_sym`` filename (case-insensitive).
    * File whose stem contains ``part_number`` as a substring.
    """
    target = part_number.strip().lower()
    if not target:
        return None
    for d in search_dirs:
        if not d.exists() or not d.is_dir():
            continue
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        # Exact match wins.
        for p in entries:
            if p.suffix.lower() == ".kicad_sym" and p.stem.lower() == target:
                return p
        # Substring match.
        for p in entries:
            if p.suffix.lower() == ".kicad_sym" and target in p.stem.lower():
                return p
    return None


def _invoke_pin2json(
    target: Path | str, *, is_lcsc: bool = False
) -> Optional[dict[str, Any]]:
    """Resolve pin data via the ``pin2json`` module.

    Delegates to :func:`pin2json.resolve_lcsc` for LCSC ids and
    :func:`pin2json.parse_kicad_sym` for existing symbol files.
    Returns ``None`` on every failure so callers can treat resolution as
    best-effort.
    """
    try:
        if is_lcsc:
            return chip_resolver_mod.resolve_lcsc(str(target))
        target_str = str(target)
        if os.path.exists(target_str):
            symbol_name, pins = chip_resolver_mod.parse_kicad_sym(target_str)
            pin_dict = {p["number"]: p["name"] for p in pins}
            sym = symbol_name or target_str
            return {
                "symbol": chip_resolver_mod.clean_symbol_name(sym),
                "package": "",
                "source": target_str,
                "pin_count": len(pins),
                "pins": pin_dict,
            }
    except Exception:
        return None
    return None


def _replace_pin(pin: Any, *, name: Optional[str]) -> Any:
    """Return a copy of ``pin`` with ``name`` replaced; tolerates frozen dataclass."""
    try:
        from dataclasses import replace

        return replace(pin, name=name)
    except Exception:
        # Fall back to object.__setattr__ for frozen dataclasses.
        try:
            object.__setattr__(pin, "name", name)
            return pin
        except Exception:
            return pin


# Re-export a small helper for symmetry with enrichers.
def component_part_numbers(components: Iterable[Any]) -> list[str]:
    """Return the non-empty ``part_number`` of each component, deduped, preserving order."""
    seen: set[str] = set()
    out: list[str] = []
    for c in components:
        pn = getattr(c, "part_number", None)
        if pn and pn not in seen:
            seen.add(pn)
            out.append(pn)
    return out


__all__ = [
    "ComponentDatabase",
    "ComponentRecord",
    "SCHEMA_DDL",
    "_parse_kicad_netlist",
    "_find_kicad_symbols",
    "_invoke_pin2json",
]
