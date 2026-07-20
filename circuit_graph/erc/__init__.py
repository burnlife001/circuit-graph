"""ERC engine — kicad-cli wrapper (Level 1) and pure-Python S-expression parser
+ ERC engine (Level 2).

Public API:

- :class:`ElectricalPinType` — KiCad's 12 pin types
- :class:`PinContext` — pin on a component, with absolute coords
- :class:`ErcViolation`, :class:`ErcReport` — engine output
- :class:`PinConflictMatrix` — 12x12 type compatibility table
- :class:`PinSeverity` — IntEnum result of a matrix check
- :class:`ElectricalRulesChecker` — orchestrator that fans out to validators
"""
from __future__ import annotations

from circuit_graph.erc.engine import (
    BaseValidator,
    ComponentValidator,
    ConnectivityValidator,
    ElectricalRulesChecker,
    PinTypeValidator,
    PowerValidator,
)
from circuit_graph.erc.kicad_cli import KicadCliNotFoundError, KicadCliRunner
from circuit_graph.erc.models import (
    ElectricalPinType,
    ErcReport,
    ErcViolation,
    PinContext,
)
from circuit_graph.erc.pin_matrix import PinConflictMatrix, PinSeverity

__all__ = [
    "ElectricalPinType",
    "PinContext",
    "ErcViolation",
    "ErcReport",
    "PinConflictMatrix",
    "PinSeverity",
    "KicadCliRunner",
    "KicadCliNotFoundError",
    "BaseValidator",
    "ComponentValidator",
    "ConnectivityValidator",
    "ElectricalRulesChecker",
    "PinTypeValidator",
    "PowerValidator",
]
