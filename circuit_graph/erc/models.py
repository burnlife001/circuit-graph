"""Data structures for the ERC engine."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Literal


class StrEnum(str, Enum):
    """String-valued enum. str(ElectricalPinType.X) == X.value."""

    def __str__(self) -> str:
        return str(self.value)


class ElectricalPinType(StrEnum):
    INPUT = "input"
    OUTPUT = "output"
    BIDIRECTIONAL = "bidirectional"
    TRISTATE = "tristate"
    PASSIVE = "passive"
    FREE = "free"
    UNSPECIFIED = "unspecified"
    POWER_IN = "power_in"
    POWER_OUT = "power_out"
    OPEN_COLLECTOR = "open_collector"
    OPEN_EMITTER = "open_emitter"
    NO_CONNECT = "no_connect"


@dataclass(frozen=True)
class PinContext:
    pin_number: str
    pin_name: str | None
    electrical_type: ElectricalPinType
    component_ref: str
    component_lib_id: str
    position: tuple[float, float]
    sheet_path: str = "/"


Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class ErcViolation:
    severity: Severity
    type: str
    code: str
    message: str
    net_name: str | None = None
    component_refs: list[str] = field(default_factory=list)
    pin_numbers: list[str] = field(default_factory=list)
    position: tuple[float, float] | None = None
    sheet_path: str = "/"
    suggested_fix: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ErcReport:
    source_file: str
    engine: Literal["kicad-cli", "native"]
    violations: list[ErcViolation] = field(default_factory=list)
    duration_ms: float = 0.0
    kicad_version: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for v in self.violations if v.severity == "warning")

    def to_dict(self) -> dict:
        return {
            "source_file": self.source_file,
            "engine": self.engine,
            "kicad_version": self.kicad_version,
            "violations": [v.to_dict() for v in self.violations],
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "duration_ms": self.duration_ms,
            "warnings": list(self.warnings),
        }
