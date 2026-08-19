"""Layout-schematic format dispatcher.

Single entry-point that picks the right backend based on file extension:

- ``.kicad_sch`` → :func:`circuit_graph.parsers.kicad_sch.parse_kicad_sch`
- ``.NET``      → :func:`circuit_graph.layout_check.protel_adapter.parse_protel`

All failures (unknown extension, missing file, malformed input) are
funneled into a single ``LayoutCheckError(kind="schematic_parse_error")``
so the orchestrator only needs to handle one error case for the parse
phase.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from circuit_graph.layout_check.loader import LayoutCheckError
from circuit_graph.layout_check.model import LayoutSchematic

# Suffixes are matched case-insensitively (``.kicad_sch`` == ``.KICAD_SCH`` == ``.KiCad_Sch``).
_KICAD_SUFFIXES: frozenset[str] = frozenset({".kicad_sch"})
_PROTEL_SUFFIXES: frozenset[str] = frozenset({".net"})


def parse_schematic(path: Path) -> LayoutSchematic:
    """Dispatch by extension; return a :class:`LayoutSchematic`.

    Raises
    ------
    LayoutCheckError
        With ``kind="schematic_parse_error"`` for unknown extension, missing
        file, or any backend parse failure. Caller (orchestrator) decides
        whether to wrap into a JSON error report or surface directly.
    """
    p = Path(path)
    suffix = p.suffix.lower()

    if suffix in _KICAD_SUFFIXES:
        from circuit_graph.parsers.kicad_sch import parse_kicad_sch

        backend: Callable[[Path], LayoutSchematic] = parse_kicad_sch
    elif suffix in _PROTEL_SUFFIXES:
        from circuit_graph.layout_check.protel_adapter import parse_protel

        backend = parse_protel
    else:
        raise LayoutCheckError(
            kind="schematic_parse_error",
            message=f"unsupported schematic extension: {suffix!r}",
            location=str(p),
            cause_path="$",
        )

    try:
        return backend(p)
    except LayoutCheckError:
        # Backend already raised a structured error — propagate as-is.
        raise
    except FileNotFoundError as e:
        raise LayoutCheckError(
            kind="schematic_parse_error",
            message=f"schematic file not found: {p}",
            location=str(p),
            cause_path="$",
        ) from e
    except Exception as e:
        raise LayoutCheckError(
            kind="schematic_parse_error",
            message=f"{type(e).__name__}: {e}",
            location=str(p),
            cause_path="$",
        ) from e
