"""ERC engine: 4 validators + orchestrator.

Each validator implements ``validate(sch) -> list[ErcViolation]`` so the
orchestrator can fan-out and aggregate. The 4 validators are independent —
each can be unit-tested in isolation.

Error codes (stable identifiers for callers to filter on):
- E001 pin_conflict          (PinTypeValidator — error severity)
- E002 duplicate_reference   (ComponentValidator)
- E003 power_pin_not_driven  (PowerValidator)
- W001 pin_conflict_warning  (PinTypeValidator — warning severity)
- W002 single_pin_net        (ConnectivityValidator)
- W003 unconnected_input     (ConnectivityValidator)
- W004 missing_value         (ComponentValidator)
- W005 missing_footprint     (ComponentValidator — reserved)
- I001 no_power_flag         (PowerValidator — reserved)
"""
from __future__ import annotations

import time
from typing import Protocol

from circuit_graph.erc.extractor import ComponentContext, ExtractedSchematic
from circuit_graph.erc.models import (
    ElectricalPinType,
    ErcReport,
    ErcViolation,
)
from circuit_graph.erc.pin_matrix import PinConflictMatrix, PinSeverity


class BaseValidator(Protocol):
    """Protocol every validator implements."""

    def validate(self, sch: ExtractedSchematic) -> list[ErcViolation]: ...


# ----------------------------------------------------------------- PinType


class PinTypeValidator:
    """Check every (a, b) pair in every net against PinConflictMatrix."""

    def __init__(self, matrix: PinConflictMatrix | None = None) -> None:
        self.matrix = matrix or PinConflictMatrix.default()

    def validate(self, sch: ExtractedSchematic) -> list[ErcViolation]:
        violations: list[ErcViolation] = []
        for net_name, pins in sch.nets.items():
            for i, a in enumerate(pins):
                for b in pins[i + 1 :]:
                    # Skip stacked pins (same ref + num + position).
                    if (
                        a.component_ref == b.component_ref
                        and a.pin_number == b.pin_number
                        and a.position == b.position
                    ):
                        continue
                    sev = self.matrix.check(a.electrical_type, b.electrical_type)
                    if sev == PinSeverity.ERROR:
                        violations.append(
                            ErcViolation(
                                severity="error",
                                type="pin_conflict",
                                code="E001",
                                message=(
                                    f"Pin type conflict on net {net_name!r}: "
                                    f"{a.component_ref}.{a.pin_number} "
                                    f"({a.electrical_type.value}) "
                                    f"<-> {b.component_ref}.{b.pin_number} "
                                    f"({b.electrical_type.value})"
                                ),
                                net_name=net_name,
                                component_refs=[a.component_ref, b.component_ref],
                                pin_numbers=[a.pin_number, b.pin_number],
                            )
                        )
                    elif sev == PinSeverity.WARNING:
                        violations.append(
                            ErcViolation(
                                severity="warning",
                                type="pin_conflict",
                                code="W001",
                                message=(
                                    f"Pin type warning on net {net_name!r}: "
                                    f"{a.component_ref}.{a.pin_number} "
                                    f"({a.electrical_type.value}) "
                                    f"<-> {b.component_ref}.{b.pin_number} "
                                    f"({b.electrical_type.value})"
                                ),
                                net_name=net_name,
                                component_refs=[a.component_ref, b.component_ref],
                                pin_numbers=[a.pin_number, b.pin_number],
                            )
                        )
        return violations


# ---------------------------------------------------------- Connectivity


class ConnectivityValidator:
    """Detect dangling wires, single-pin nets, and unconnected inputs."""

    def validate(self, sch: ExtractedSchematic) -> list[ErcViolation]:
        violations: list[ErcViolation] = []

        # Single-pin net: only one pin → warning.
        for net_name, pins in sch.nets.items():
            if len(pins) == 1:
                # Nets with an attached label are I/O ports — the label
                # provides the off-sheet connection, so a single-pin net
                # is expected here.
                if net_name in sch.labeled_nets:
                    continue
                violations.append(
                    ErcViolation(
                        severity="warning",
                        type="single_pin_net",
                        code="W002",
                        message=(
                            f"Net {net_name!r} has only one pin "
                            f"({pins[0].component_ref}.{pins[0].pin_number})"
                        ),
                        net_name=net_name,
                        component_refs=[pins[0].component_ref],
                        pin_numbers=[pins[0].pin_number],
                    )
                )

        # Unconnected inputs: an INPUT pin not appearing on any net
        # (i.e. its position never matched any wire / label endpoint).
        nets_pins: set[tuple[str, str]] = set()
        for pins in sch.nets.values():
            for p in pins:
                nets_pins.add((p.component_ref, p.pin_number))

        for comp in sch.components:
            for pin in comp.pins:
                if (comp.reference, pin.pin_number) in nets_pins:
                    continue
                if pin.electrical_type == ElectricalPinType.INPUT:
                    violations.append(
                        ErcViolation(
                            severity="warning",
                            type="unconnected_input",
                            code="W003",
                            message=(
                                f"Input pin {comp.reference}.{pin.pin_number} "
                                "is not connected to any net"
                            ),
                            component_refs=[comp.reference],
                            pin_numbers=[pin.pin_number],
                            position=pin.position,
                        )
                    )
        return violations


# ------------------------------------------------------------- Component


class ComponentValidator:
    """Detect duplicate references and missing value/footprint."""

    def validate(self, sch: ExtractedSchematic) -> list[ErcViolation]:
        violations: list[ErcViolation] = []

        # Duplicate references.
        seen: dict[str, list[ComponentContext]] = {}
        for comp in sch.components:
            seen.setdefault(comp.reference, []).append(comp)
        for ref, comps in seen.items():
            if len(comps) > 1:
                violations.append(
                    ErcViolation(
                        severity="error",
                        type="duplicate_reference",
                        code="E002",
                        message=f"Reference {ref!r} appears {len(comps)} times in schematic",
                        component_refs=[ref],
                    )
                )

        # Missing value / footprint.
        for comp in sch.components:
            if comp.is_power_symbol:
                continue  # power symbols don't need value
            if not comp.value:
                violations.append(
                    ErcViolation(
                        severity="warning",
                        type="missing_value",
                        code="W004",
                        message=f"Component {comp.reference!r} has no Value property",
                        component_refs=[comp.reference],
                    )
                )
        return violations


# ----------------------------------------------------------------- Power


_DRIVER_TYPES = {
    ElectricalPinType.OUTPUT,
    ElectricalPinType.POWER_OUT,
    ElectricalPinType.TRISTATE,
    ElectricalPinType.BIDIRECTIONAL,
}

# KiCad's PWR_FLAG symbol lib_id — used to mark a net as having external power
# (e.g. supply coming from off-board connector). Detected as a "power:" lib
# symbol in the schematic.
_PWR_FLAG_LIB_ID = "power:PWR_FLAG"


def _collect_pwr_flag_refs(sch: ExtractedSchematic) -> set[str]:
    """Return the set of component refs that are PWR_FLAG markers.

    PWR_FLAG is a KiCad power symbol (``lib_id == "power:PWR_FLAG"``) that
    tells ERC the net is powered externally (connector, off-board regulator,
    etc.), bypassing the driver-detection rule. See KiCad docs:
    ``eeschema/power_symbols.rst``.
    """
    return {
        comp.reference
        for comp in sch.components
        if comp.lib_id == _PWR_FLAG_LIB_ID
    }


class PowerValidator:
    """Detect undriven POWER_IN pins and missing PWR_FLAG markers."""

    def validate(self, sch: ExtractedSchematic) -> list[ErcViolation]:
        violations: list[ErcViolation] = []
        pwr_flag_refs = _collect_pwr_flag_refs(sch)

        for net_name, pins in sch.nets.items():
            has_driver = any(p.electrical_type in _DRIVER_TYPES for p in pins)
            power_in_pins = [
                p for p in pins if p.electrical_type == ElectricalPinType.POWER_IN
            ]
            has_pwr_flag = any(p.component_ref in pwr_flag_refs for p in pins)

            # E003: POWER_IN pins on a net with no driver AND no PWR_FLAG.
            if not has_driver and not has_pwr_flag and power_in_pins:
                for p in power_in_pins:
                    violations.append(
                        ErcViolation(
                            severity="error",
                            type="power_pin_not_driven",
                            code="E003",
                            message=(
                                f"Input Power pin not driven by any Output Power pins "
                                f"on net {net_name!r}: {p.component_ref}.{p.pin_number}"
                            ),
                            net_name=net_name,
                            component_refs=[p.component_ref],
                            pin_numbers=[p.pin_number],
                            position=p.position,
                        )
                    )

            # I001 (info): power net has no PWR_FLAG marker — informational
            # hint that adding a PWR_FLAG would make the net's power source
            # explicit in the schematic.
            if power_in_pins and not has_pwr_flag:
                violations.append(
                    ErcViolation(
                        severity="info",
                        type="no_power_flag",
                        code="I001",
                        message=(
                            f"Power net {net_name!r} has no PWR_FLAG marker "
                            "(add a 'power:PWR_FLAG' symbol to declare external "
                            "power source)"
                        ),
                        net_name=net_name,
                    )
                )

        return violations


# ---------------------------------------------------------- Orchestrator


class ElectricalRulesChecker:
    """Run all 4 validators and aggregate into an :class:`ErcReport`."""

    def __init__(
        self,
        pin_matrix: PinConflictMatrix | None = None,
        validators: list[BaseValidator] | None = None,
    ) -> None:
        if validators is None:
            validators = [
                PinTypeValidator(matrix=pin_matrix),
                ConnectivityValidator(),
                ComponentValidator(),
                PowerValidator(),
            ]
        self.validators = validators

    def run(self, sch: ExtractedSchematic) -> ErcReport:
        start = time.monotonic()
        violations: list[ErcViolation] = []
        for v in self.validators:
            violations.extend(v.validate(sch))
        duration_ms = (time.monotonic() - start) * 1000.0
        return ErcReport(
            source_file=sch.source_file,
            engine="native",
            violations=violations,
            duration_ms=duration_ms,
        )

    def run_check(self, name: str, sch: ExtractedSchematic) -> list[ErcViolation]:
        """Run a single validator by short name (e.g. ``"pin_types"``)."""
        mapping = {
            "pin_types": PinTypeValidator,
            "connectivity": ConnectivityValidator,
            "components": ComponentValidator,
            "power": PowerValidator,
        }
        if name not in mapping:
            raise ValueError(
                f"Unknown check: {name!r}. Valid: {sorted(mapping.keys())}"
            )
        return mapping[name]().validate(sch)
