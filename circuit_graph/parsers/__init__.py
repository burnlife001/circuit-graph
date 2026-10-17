"""Netlist parser interfaces and implementations."""

from circuit_graph.parsers.protel import ParserInput, ProtelNetParser
from circuit_graph.parsers.registry import (
    NetlistParser,
    detect_parser,
    parse_netlist,
    register_parser,
)

__all__ = [
    "NetlistParser",
    "ParserInput",
    "ProtelNetParser",
    "detect_parser",
    "parse_netlist",
    "register_parser",
]
