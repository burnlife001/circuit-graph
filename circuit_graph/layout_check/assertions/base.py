"""Assertion ABC + spatial helpers shared by all 12 built-in assertions.

**Boundary semantics (uniform across all assertions):**
    distance <= tolerance_mm  -> PASS
    distance >  tolerance_mm  -> VIOLATION

This is documented here once and reused in every concrete assertion.
"""
from __future__ import annotations

import math
import re
from abc import ABC, abstractmethod
from typing import Any, ClassVar

from circuit_graph.layout_check.model import (
    AppliesTo,
    LayoutSchematic,
    Violation,
)

_REF_PREFIX_RE = re.compile(r"^#?([A-Za-z]+)")


def refdes_prefix(comp_id: str) -> str:
    """Extract the leading alphabetic prefix of a component id.

    Examples
    --------
    >>> refdes_prefix("R96")
    'R'
    >>> refdes_prefix("U1")
    'U'
    >>> refdes_prefix("#PWR03")
    'PWR'
    >>> refdes_prefix("123abc")
    ''

    The leading optional ``#`` (used by KiCad for power/flag symbols) is
    stripped, then the contiguous alphabetic run is returned.
    """
    m = _REF_PREFIX_RE.match(comp_id or "")
    return m.group(1) if m else ""


def match_glob(pattern: str, value: str) -> bool:
    """``fnmatch.fnmatchcase`` wrapper. ``*`` matches any, ``?`` matches one char.

    Empty pattern matches only empty value (fnmatch convention).
    """
    import fnmatch
    return fnmatch.fnmatchcase(value, pattern)


def passes_or_violates(distance: float, tolerance_mm: float) -> bool:
    """Boundary check. Returns True if the assertion passes.

    Convention: ``distance <= tolerance_mm`` passes; ``> tolerance_mm`` fails.
    """
    return distance <= tolerance_mm


def distance_mm(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Euclidean distance in mm between two (x, y) points."""
    return math.hypot(p1[0] - p2[0], p1[1] - p2[1])


class Assertion(ABC):
    """Abstract base class for all 12 built-in layout assertions.

    Subclasses must set the ``kind`` ClassVar to one of
    :data:`circuit_graph.layout_check.model.BUILT_IN_ASSERTION_KINDS`.

    Concrete assertions override :meth:`evaluate` and emit :class:`Violation`
    records for each layout rule breach.
    """

    kind: ClassVar[str]

    @abstractmethod
    def evaluate(
        self,
        schematic: LayoutSchematic,
        applies: AppliesTo | None,
        params: dict[str, Any],
    ) -> list[Violation]:
        """Run the assertion and return the list of violations (may be empty)."""
        raise NotImplementedError
