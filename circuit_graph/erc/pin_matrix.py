"""Pin electrical-type conflict matrix."""
from __future__ import annotations

from enum import IntEnum

from circuit_graph.erc.models import ElectricalPinType


class PinSeverity(IntEnum):
    OK = 0
    WARNING = 1
    ERROR = 2


class PinConflictMatrix:
    def __init__(self) -> None:
        self._matrix = self._build_default()

    @classmethod
    def default(cls) -> "PinConflictMatrix":
        return cls()

    @staticmethod
    def _build_default() -> dict[tuple[ElectricalPinType, ElectricalPinType], PinSeverity]:
        m: dict[tuple[ElectricalPinType, ElectricalPinType], PinSeverity] = {}
        all_types = list(ElectricalPinType)

        for a in all_types:
            for b in all_types:
                m[(a, b)] = PinSeverity.OK
                m[(b, a)] = PinSeverity.OK

        error_pairs = [
            (ElectricalPinType.OUTPUT, ElectricalPinType.OUTPUT),
            (ElectricalPinType.POWER_OUT, ElectricalPinType.POWER_OUT),
            (ElectricalPinType.OUTPUT, ElectricalPinType.POWER_OUT),
        ]
        for a, b in error_pairs:
            m[(a, b)] = PinSeverity.ERROR
            m[(b, a)] = PinSeverity.ERROR

        for t in all_types:
            if t in (ElectricalPinType.PASSIVE, ElectricalPinType.FREE):
                continue
            m[(ElectricalPinType.UNSPECIFIED, t)] = PinSeverity.WARNING
            m[(t, ElectricalPinType.UNSPECIFIED)] = PinSeverity.WARNING

        for t in all_types:
            if t == ElectricalPinType.FREE:
                continue
            m[(ElectricalPinType.NO_CONNECT, t)] = PinSeverity.ERROR
            m[(t, ElectricalPinType.NO_CONNECT)] = PinSeverity.ERROR

        return m

    def check(self, t1: ElectricalPinType, t2: ElectricalPinType) -> PinSeverity:
        return self._matrix[(t1, t2)]

    def set_rule(self, t1: ElectricalPinType, t2: ElectricalPinType, severity: PinSeverity) -> None:
        self._matrix[(t1, t2)] = severity
        self._matrix[(t2, t1)] = severity
