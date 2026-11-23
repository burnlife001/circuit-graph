"""Registry routing tests: detection, fallback, and end-to-end parse."""

from pathlib import Path

from circuit_graph.parsers import (
    ParserInput,
    ProtelNetParser,
    detect_parser,
    parse_netlist,
)

FIXTURE = (
    Path(__file__).parent.parent.parent
    / "fixtures"
    / "protel"
    / "FX-T268-V6-10米-5065.NET"
)


def test_registry_selects_protel_by_suffix():
    parser = detect_parser(ParserInput(file_path="board.NET"))
    assert isinstance(parser, ProtelNetParser)


def test_registry_selects_protel_by_content():
    content = "[\nR1\n0805\n10K\n]\n(\nGND\nR1.2\n)\n"
    parser = detect_parser(ParserInput(content=content))
    assert isinstance(parser, ProtelNetParser)


def test_registry_falls_back_to_protel():
    """Nothing matches → Protel default, preserving historical behavior."""
    parser = detect_parser(ParserInput(file_path="notes.txt", content="hello"))
    assert isinstance(parser, ProtelNetParser)


def test_parse_netlist_end_to_end_t268():
    ir = parse_netlist(ParserInput(source_id=str(FIXTURE), file_path=str(FIXTURE)))
    assert ir.components, "expected components from the T268 fixture"
    assert ir.nets, "expected nets from the T268 fixture"
