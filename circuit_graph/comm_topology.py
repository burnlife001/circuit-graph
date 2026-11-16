"""Communication topology recognizer.

Detects common communication buses (I2C, UART, SPI, PWM) from net names and
connection structure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from circuit_graph.ir import CircuitIR, Component, Net

BusProtocol = Literal["i2c", "uart", "spi", "pwm", "unknown"]


@dataclass(frozen=True)
class BusMember:
    """A component/pin pair that participates in a bus."""

    component_id: str
    pin_name: str | None = None
    role: Literal["master", "slave", "controller", "driver", "unknown"] = "unknown"


@dataclass(frozen=True)
class CommBus:
    """A detected communication bus."""

    id: str
    protocol: BusProtocol
    nets: list[str]
    members: list[BusMember]
    confidence: Literal["high", "medium", "low", "unknown"]
    evidence: list[str] = field(default_factory=list)
    master_id: str | None = None
    slave_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CommTopologyResult:
    """Result of communication topology detection."""

    buses: list[CommBus] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Pattern configuration
# ---------------------------------------------------------------------------

# ``required_aliases`` maps raw net-name suffixes (uppercase) to a canonical
# signal name understood by ``pin_roles``.  This lets us accept both ``TX`` and
# ``TXD`` while keeping downstream logic simple.
BUS_PATTERNS: dict[BusProtocol, dict[str, Any]] = {
    "i2c": {
        "required_aliases": {"SDA": "SDA", "SCL": "SCL"},
        "pin_roles": {"SDA": "slave", "SCL": "slave"},
        "min_components": 2,
    },
    "uart": {
        "required_aliases": {
            "TX": "TX",
            "RX": "RX",
            "TXD": "TX",
            "RXD": "RX",
        },
        "pin_roles": {"TX": "slave", "RX": "slave"},
        "min_components": 2,
    },
    "spi": {
        "required_aliases": {
            "MOSI": "MOSI",
            "MISO": "MISO",
            "SCK": "SCK",
            "CLK": "SCK",
            "CS": "CS",
            "SS": "CS",
            "NSS": "CS",
        },
        "required_signals": {"MOSI", "MISO", "SCK"},
        "pin_roles": {
            "MOSI": "slave",
            "MISO": "slave",
            "SCK": "slave",
            "CS": "slave",
        },
        "min_components": 2,
    },
    "pwm": {
        "net_pattern": re.compile(r"^(.*?)PWM(N?)(\d*)$", re.IGNORECASE),
        "min_components": 2,
    },
}


def detect_comm_topology(ir: CircuitIR) -> CommTopologyResult:
    """Detect communication buses in ``ir``.

    Returns a :class:`CommTopologyResult` containing detected buses and any
    warnings.  The input IR is not mutated.
    """
    warnings: list[str] = []
    comp_map: dict[str, Component] = {c.id: c for c in ir.components}

    # Index connections by net for fast component lookup.
    net_to_pins: dict[str, list[str]] = {}
    for conn in ir.connections:
        net_to_pins.setdefault(conn.net_id, []).append(conn.pin_id)

    def components_on_net(net_id: str) -> list[Component]:
        pin_ids = net_to_pins.get(net_id, [])
        result: list[Component] = []
        seen: set[str] = set()
        for pin_id in pin_ids:
            comp_id = pin_id.rsplit(".", 1)[0]
            if comp_id in seen or comp_id not in comp_map:
                continue
            seen.add(comp_id)
            result.append(comp_map[comp_id])
        return result

    buses: list[CommBus] = []

    # I2C / UART / SPI detection by required net name groups.
    for protocol in ("i2c", "uart", "spi"):
        pattern = BUS_PATTERNS[protocol]
        aliases: dict[str, str] = pattern["required_aliases"]
        required_signals: set[str] = pattern.get("required_signals", set(aliases.values()))
        pin_roles: dict[str, str] = pattern["pin_roles"]
        min_components: int = pattern["min_components"]

        # Build candidate groups keyed by a common prefix (net name minus suffix).
        groups: dict[str, dict[str, Net]] = {}
        for net in ir.nets:
            match = _match_alias(net.name.upper(), aliases)
            if match is None:
                continue
            suffix, canonical = match
            prefix = net.name[: -len(suffix)].rstrip("_/-") if suffix else net.name
            groups.setdefault(prefix, {})[canonical] = net

        for prefix, net_group in groups.items():
            matched_signals = set(net_group)
            if not required_signals <= matched_signals:
                continue
            matched_nets = [net_group[s] for s in sorted(matched_signals)]

            all_components: dict[str, Component] = {}
            members: list[BusMember] = []
            for net in matched_nets:
                match = _match_alias(net.name.upper(), aliases)
                canonical = match[1] if match else ""
                for comp in components_on_net(net.id):
                    all_components[comp.id] = comp
                    role = pin_roles.get(canonical, "unknown")
                    members.append(BusMember(component_id=comp.id, pin_name=None, role=role))

            if len(all_components) < min_components:
                warnings.append(
                    f"COMM_LOW_COMPONENT_COUNT: {protocol} "
                    f"group '{prefix}' has {len(all_components)} component(s), "
                    f"need {min_components}"
                )
                continue

            # Deduplicate members by component id; if a component appears on
            # multiple nets keep the most specific known role.
            seen_members: dict[str, BusMember] = {}
            for m in members:
                existing = seen_members.get(m.component_id)
                if existing is None or existing.role == "unknown":
                    seen_members[m.component_id] = m
            unique_members = list(seen_members.values())

            bus_id = f"{protocol}-{prefix.lower() or 'bus'}"
            master_id, slave_ids = _infer_master_slaves(
                list(all_components.values()),
                protocol,
            )
            confidence = _bus_confidence(
                protocol,
                len(all_components),
                master_id,
                matched_nets,
            )
            evidence = _build_evidence(protocol, matched_nets, all_components, master_id)

            # Update member roles from inferred master/slaves.
            updated_members = [
                BusMember(
                    component_id=m.component_id,
                    pin_name=m.pin_name,
                    role=(
                        "master"
                        if m.component_id == master_id
                        else "slave"
                        if m.component_id in slave_ids
                        else m.role
                    ),
                )
                for m in unique_members
            ]

            buses.append(
                CommBus(
                    id=bus_id,
                    protocol=protocol,
                    nets=[n.name for n in matched_nets],
                    members=updated_members,
                    confidence=confidence,
                    evidence=evidence,
                    master_id=master_id,
                    slave_ids=slave_ids,
                )
            )

    # PWM detection.
    buses.extend(_detect_pwm_buses(ir.nets, components_on_net, comp_map))

    return CommTopologyResult(buses=buses, warnings=warnings)


def _match_alias(name_upper: str, aliases: dict[str, str]) -> tuple[str, str] | None:
    """Return ``(matched_suffix, canonical_signal)`` if ``name_upper`` ends with
    one of the configured aliases, or ``None``.
    """
    best_suffix: str | None = None
    best_canonical: str | None = None
    for suffix, canonical in aliases.items():
        if not name_upper.endswith(suffix):
            continue
        # Require a word boundary unless the suffix consumes the whole name.
        if len(name_upper) > len(suffix):
            sep = name_upper[-len(suffix) - 1]
            if sep.isalnum():
                continue
        if best_suffix is None or len(suffix) > len(best_suffix):
            best_suffix = suffix
            best_canonical = canonical
    if best_suffix is None or best_canonical is None:
        return None
    return best_suffix, best_canonical


def _infer_master_slaves(
    components: list[Component],
    protocol: BusProtocol,
) -> tuple[str | None, list[str]]:
    """Infer master and slave components for a bus.

    Returns ``(master_id, slave_ids)``.  Master inference is deliberately weak;
    absence of strong evidence returns ``(None, [])``.
    """
    if protocol not in ("i2c", "uart", "spi"):
        return None, []

    candidates = [c for c in components if c.device_kind.value == "IC"]
    if not candidates:
        return None, []

    # Heuristic: the IC with the most pins is the likely master/central device.
    best = max(candidates, key=lambda c: len(c.pins))
    master_id = best.id
    slave_ids = [c.id for c in components if c.id != master_id]
    return master_id, slave_ids


def _bus_confidence(
    protocol: BusProtocol,
    component_count: int,
    master_id: str | None,
    nets: list[Net],
) -> Literal["high", "medium", "low", "unknown"]:
    """Assign a confidence level to a detected bus."""
    if component_count < 2:
        return "unknown"
    if protocol in ("i2c", "uart"):
        if master_id is not None and len(nets) >= 2:
            return "high"
        return "medium"
    if protocol == "spi":
        if master_id is not None and len(nets) >= 3:
            return "high"
        return "medium"
    if protocol == "pwm":
        return "medium" if component_count >= 2 else "low"
    return "unknown"


def _build_evidence(
    protocol: BusProtocol,
    nets: list[Net],
    components: dict[str, Component],
    master_id: str | None,
) -> list[str]:
    """Build human-readable evidence strings."""
    evidence = [
        f"protocol={protocol}",
        f"nets={','.join(n.name for n in nets)}",
        f"components={','.join(sorted(components))}",
    ]
    if master_id:
        evidence.append(f"master={master_id}")
    return evidence


def _detect_pwm_buses(
    nets: list[Net],
    components_on_net: Any,
    comp_map: dict[str, Component],
) -> list[CommBus]:
    """Detect PWM channels / complementary pairs from net names."""
    pattern = BUS_PATTERNS["pwm"]["net_pattern"]
    pwm_nets: list[tuple[Net, re.Match[str]]] = []
    for net in nets:
        m = pattern.match(net.name)
        if m:
            pwm_nets.append((net, m))

    buses: list[CommBus] = []
    consumed: set[str] = set()

    # Group complementary pairs (PWM + PWMN sharing prefix+index).
    pair_index: dict[tuple[str, str], dict[str, Net]] = {}
    for net, m in pwm_nets:
        prefix = m.group(1) or ""
        index = m.group(3) or ""
        is_n = bool(m.group(2))
        key = (prefix, index)
        pair_index.setdefault(key, {})["N" if is_n else "P"] = net

    for (prefix, index), pair in pair_index.items():
        if len(pair) < 1:
            continue
        matched_nets = list(pair.values())
        all_components: dict[str, Component] = {}
        members: list[BusMember] = []
        for net in matched_nets:
            for comp in components_on_net(net.id):
                if comp.id not in all_components:
                    all_components[comp.id] = comp
                    role: Literal["controller", "driver", "unknown"] = _pwm_component_role(comp)
                    members.append(BusMember(component_id=comp.id, role=role))

        if len(all_components) < BUS_PATTERNS["pwm"]["min_components"]:
            continue

        suffix = f"{prefix}pwm{index}".lower()
        bus_id = f"pwm-{suffix}"
        ic_members = [c for c in all_components.values() if c.device_kind.value == "IC"]
        master_id: str | None = None
        if ic_members:
            master_id = max(ic_members, key=lambda c: len(c.pins)).id
        slave_ids = [c.id for c in all_components.values() if c.id != master_id]

        for net in matched_nets:
            consumed.add(net.id)

        buses.append(
            CommBus(
                id=bus_id,
                protocol="pwm",
                nets=[n.name for n in matched_nets],
                members=[
                    BusMember(
                        component_id=m.component_id,
                        role=(
                            "master"
                            if m.component_id == master_id
                            else "slave"
                            if m.component_id in slave_ids
                            else m.role
                        ),
                    )
                    for m in members
                ],
                confidence="high" if len(pair) == 2 and master_id else "medium",
                evidence=_build_evidence("pwm", matched_nets, all_components, master_id),
                master_id=master_id,
                slave_ids=slave_ids,
            )
        )

    # Single PWM nets that were not consumed by a pair are still reported if
    # they connect at least two components.
    for net, _m in pwm_nets:
        if net.id in consumed:
            continue
        components = components_on_net(net.id)
        if len(components) < BUS_PATTERNS["pwm"]["min_components"]:
            continue
        ic_members = [c for c in components if c.device_kind.value == "IC"]
        master_id = max(ic_members, key=lambda c: len(c.pins)).id if ic_members else None
        slave_ids = [c.id for c in components if c.id != master_id]
        buses.append(
            CommBus(
                id=f"pwm-{net.name.lower()}",
                protocol="pwm",
                nets=[net.name],
                members=[
                    BusMember(
                        component_id=c.id,
                        role=(
                            "master"
                            if c.id == master_id
                            else "slave"
                            if c.id in slave_ids
                            else _pwm_component_role(c)
                        ),
                    )
                    for c in components
                ],
                confidence="low",
                evidence=_build_evidence("pwm", [net], {c.id: c for c in components}, master_id),
                master_id=master_id,
                slave_ids=slave_ids,
            )
        )

    return buses


def _pwm_component_role(comp: Component) -> Literal["controller", "driver", "unknown"]:
    """Classify a component's likely role on a PWM net."""
    if comp.device_kind.value == "IC":
        return "controller"
    if comp.device_kind.value == "Mosfet":
        return "driver"
    return "unknown"
