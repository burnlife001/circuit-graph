"""Parser registry: route netlist parsing through can_parse probing.

Callers used to hardcode ProtelNetParser; they now ask the registry to pick
a parser so new formats (KiCad, Altium, ...) can be added by registering
another implementation, without touching CLI/KB code.
"""

from __future__ import annotations

from typing import Protocol

from circuit_graph.ir import CircuitIR
from circuit_graph.parsers.protel import ParserInput, ProtelNetParser


class NetlistParser(Protocol):
    """Interface every CircuitIR-producing parser must implement."""

    name: str
    format_id: str
    extensions: set[str]

    def can_parse(self, input_: ParserInput) -> bool: ...

    def parse(self, input_: ParserInput) -> CircuitIR: ...


# Ordered registry: the first parser whose can_parse() accepts the input wins.
PARSERS: list[NetlistParser] = [ProtelNetParser()]

_DEFAULT_PARSER: NetlistParser = PARSERS[0]


def register_parser(parser: NetlistParser, *, first: bool = False) -> None:
    """Add a parser to the registry (``first=True`` probes it before others)."""
    if first:
        PARSERS.insert(0, parser)
    else:
        PARSERS.append(parser)


def detect_parser(input_: ParserInput) -> NetlistParser:
    """Return the first registered parser accepting ``input_``.

    Falls back to ProtelNetParser when nothing matches, preserving the
    historical behavior of callers that only ever handled Protel .NET files.
    """
    for parser in PARSERS:
        try:
            if parser.can_parse(input_):
                return parser
        except Exception:  # noqa: BLE001 - a broken probe must not block others
            continue
    return _DEFAULT_PARSER


def parse_netlist(input_: ParserInput) -> CircuitIR:
    """Parse with the detected parser; the single entry point for callers."""
    return detect_parser(input_).parse(input_)
