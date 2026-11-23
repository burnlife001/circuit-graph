"""Tests for ``pdf_pin_extract`` — PDF datasheet pin extraction.

Covers page search, table extraction, datasheet file matching, and the
stage 5 integration in ``_auto_resolve_ics``.

These tests mock Camelot (external venv) and PDF access — they do
NOT require a real Camelot installation or real datasheets.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from circuit_graph.knowledge.pdf_pin_extract import (
    _CAMELOT_SCRIPT,
    _find_datasheet,
    _parse_by_column_names,
    _parse_heuristic,
    extract_pin_table,
    normalize,
    search_pin_pages,
)


# ======================================================================
# Text cleaning
# ======================================================================


class TestNormalize:
    def test_collapse_whitespace(self) -> None:
        assert normalize("  Hello   World  ") == "Hello World"

    def test_strip_non_breaking_space(self) -> None:
        assert normalize("VIN\xa0OUT") == "VIN OUT"

    def test_empty(self) -> None:
        assert normalize("") == ""

    def test_none(self) -> None:
        assert normalize(None) == ""

    def test_strip_trailing_junk(self) -> None:
        assert normalize("VDD;") == "VDD"
        assert normalize("VCC,") == "VCC"
        assert normalize("Pin 1 :") == "Pin 1"


# ======================================================================
# Datasheet file matching
# ======================================================================


class TestFindDatasheet:
    def test_exact_match(self, tmp_path: Path) -> None:
        ds_dir = tmp_path / "datasheets"
        ds_dir.mkdir()
        pdf = ds_dir / "JW5065.pdf"
        pdf.write_text("dummy pdf")
        result = _find_datasheet("JW5065", ds_dir)
        assert result == pdf

    def test_exact_match_case(self, tmp_path: Path) -> None:
        ds_dir = tmp_path / "datasheets"
        ds_dir.mkdir()
        pdf = ds_dir / "jw5065.pdf"
        pdf.write_text("dummy pdf")
        result = _find_datasheet("JW5065", ds_dir)
        assert result == pdf

    def test_prefix_no_match_different_model(self, tmp_path: Path) -> None:
        """JW5065 must NOT match JW5065H.pdf (different model)."""
        ds_dir = tmp_path / "datasheets"
        ds_dir.mkdir()
        # Create a similar but different model.
        pdf = ds_dir / "JW5065H.pdf"
        pdf.write_text("dummy")
        result = _find_datasheet("JW5065", ds_dir)
        assert result is None

    def test_prefix_match_strip_suffix(self, tmp_path: Path) -> None:
        """Match when filename has a suffix after part_number (e.g., -QFP48)."""
        ds_dir = tmp_path / "datasheets"
        ds_dir.mkdir()
        pdf = ds_dir / "JW5065-QFP48.pdf"
        pdf.write_text("dummy")
        result = _find_datasheet("JW5065", ds_dir)
        assert result == pdf

    def test_prefix_match_with_subdir(self, tmp_path: Path) -> None:
        """Recursive search into subdirectories."""
        ds_dir = tmp_path / "datasheets"
        sub = ds_dir / "mfg"
        sub.mkdir(parents=True)
        pdf = sub / "JW5065.pdf"
        pdf.write_text("dummy")
        result = _find_datasheet("JW5065", ds_dir)
        assert result == pdf

    def test_no_matching_pdf(self, tmp_path: Path) -> None:
        ds_dir = tmp_path / "datasheets"
        ds_dir.mkdir()
        result = _find_datasheet("NONEXIST", ds_dir)
        assert result is None

    def test_empty_dir(self, tmp_path: Path) -> None:
        ds_dir = tmp_path / "empty"
        ds_dir.mkdir()
        result = _find_datasheet("NONEXIST", ds_dir)
        assert result is None

    def test_strip_package_suffix(self, tmp_path: Path) -> None:
        """Fuzzy match strips packaging suffixes like -SOT23."""
        ds_dir = tmp_path / "datasheets"
        ds_dir.mkdir()
        pdf = ds_dir / "JW5065-SOT23-6.pdf"
        pdf.write_text("dummy")
        result = _find_datasheet("JW5065", ds_dir)
        assert result == pdf

    def test_substring_match_middle_of_name(self, tmp_path: Path) -> None:
        """Match when part number appears as substring (e.g., DS_MM32F0140_EN.pdf)."""
        ds_dir = tmp_path / "datasheets"
        ds_dir.mkdir()
        pdf = ds_dir / "DS_MM32F0140_EN.pdf"
        pdf.write_text("dummy")
        result = _find_datasheet("MM32F0140", ds_dir)
        assert result == pdf

    def test_substring_no_match_different_model(self, tmp_path: Path) -> None:
        """MM32F0140 must NOT match MM32F0144 (different model)."""
        ds_dir = tmp_path / "datasheets"
        ds_dir.mkdir()
        pdf = ds_dir / "MM32F0144.pdf"
        pdf.write_text("dummy")
        result = _find_datasheet("MM32F0140", ds_dir)
        assert result is None


# ======================================================================
# Table parsing (unit — no PDF/Camelot needed)
# ======================================================================


class TestParseByColumnNames:
    def test_basic(self) -> None:
        header = ["Pin No.", "Pin Name", "Type"]
        rows = [
            ["1", "MODE", "I"],
            ["2", "VIN", "P"],
            ["3", "SW", "O"],
        ]
        result = _parse_by_column_names(header, rows)
        assert result == {"1": "MODE", "2": "VIN", "3": "SW"}

    def test_case_insensitive_columns(self) -> None:
        header = ["pin number", "symbol", "direction"]
        rows = [
            ["1", "VCC", "PWR"],
            ["2", "GND", "GND"],
        ]
        result = _parse_by_column_names(header, rows)
        assert result == {"1": "VCC", "2": "GND"}

    def test_chinese_columns(self) -> None:
        header = ["引脚", "名称", "说明"]
        rows = [
            ["1", "VIN", "电源输入"],
            ["2", "GND", "地"],
        ]
        # Heuristic fallback should handle this since column names won't
        # match English keywords.  Should return None from _parse_by_column_names.
        result = _parse_by_column_names(header, rows)
        assert result is None  # no English column names matched

    def test_missing_column(self) -> None:
        header = ["Pin No."]
        rows = [["1"], ["2"]]
        result = _parse_by_column_names(header, rows)
        assert result is None

    def test_empty_header(self) -> None:
        result = _parse_by_column_names([], [["1", "VCC"]])
        assert result is None

    def test_empty_rows(self) -> None:
        header = ["Pin No.", "Name"]
        rows: list[list[str]] = []
        result = _parse_by_column_names(header, rows)
        # Empty rows produce an empty pin map, which is falsy → returns None.
        assert result is None

    def test_decimal_pin_numbers(self) -> None:
        header = ["Pin", "Function"]
        rows = [["1", "VCC"], ["2", "GND"], ["3", "NC"], ["4", "OUT"]]
        result = _parse_by_column_names(header, rows)
        # "Pin" matches _NUMBER_COLS, "Function" matches _NAME_COLS
        assert result == {"1": "VCC", "2": "GND", "3": "NC", "4": "OUT"}

    def test_non_standard_headers(self) -> None:
        header = ["#", "Description", "I/O"]
        rows = [["1", "Power", "I"], ["2", "Ground", "O"]]
        result = _parse_by_column_names(header, rows)
        # "#" should match _NUMBER_COLS via "#"
        assert result is not None
        assert result.get("1") is not None

    def test_empty_cell_in_column(self) -> None:
        header = ["Pin", "Name"]
        rows = [["1", "VCC"], ["", "GND"], ["3", "OUT"]]
        result = _parse_by_column_names(header, rows)
        assert result == {"1": "VCC", "3": "OUT"}


class TestParseHeuristic:
    def test_numeric_first_col(self) -> None:
        header = ["No", "Description"]
        rows = [
            ["1", "MODE"],
            ["2", "VIN"],
            ["3", "GND"],
        ]
        result = _parse_heuristic(header, rows)
        assert result == {"1": "MODE", "2": "VIN", "3": "GND"}

    def test_no_numeric_column(self) -> None:
        header = ["Signal", "Type"]
        rows = [["VCC", "PWR"], ["GND", "GND"]]
        result = _parse_heuristic(header, rows)
        assert result is None

    def test_empty_rows(self) -> None:
        result = _parse_heuristic(["A", "B"], [])
        assert result is None

    def test_too_few_numeric(self) -> None:
        """At least 60% of values must be numeric."""
        header = ["X", "Y"]
        rows = [["A", "B"], ["C", "D"], ["1", "E"]]
        result = _parse_heuristic(header, rows)
        assert result is None  # only 1/3 numeric ~33%


# ======================================================================
# extract_pin_table — with mocked subprocess
# ======================================================================


def _mock_camelot_tables(*, pages: list[str] | None = None) -> str:
    """Build a Camelot-style JSON response for testing."""
    if pages is None:
        pages = ["1"]
    tables = []
    for page in pages:
        tables.append({
            "header": ["Pin No.", "Pin Name", "Type"],
            "rows": [
                ["1", "MODE", "I"],
                ["2", "VIN", "P"],
                ["3", "SW", "O"],
                ["4", "GND", "G"],
            ],
            "page": page,
            "accuracy": 99.5,
        })
    return json.dumps(tables)


class TestExtractPinTable:
    def test_parse_success(self, monkeypatch, tmp_path: Path) -> None:
        """Happy-path: subprocess returns valid Camelot output."""
        pdf = tmp_path / "test.pdf"
        pdf.write_text("dummy")

        import circuit_graph.knowledge.pdf_pin_extract as ppe

        def _fake_run(*args, **kwargs):
            class FakeResult:
                returncode = 0
                stdout = _mock_camelot_tables()
                stderr = ""
            return FakeResult()

        monkeypatch.setattr(ppe.subprocess, "run", _fake_run)
        # Point camelot_venv to a fake but existing dir.
        fake_venv = tmp_path / "fake_venv"
        fake_venv.mkdir()
        (fake_venv / "Scripts").mkdir()
        (fake_venv / "Scripts" / "python.exe").write_text("")
        monkeypatch.setattr(ppe, "camelot_venv", lambda: fake_venv)

        result = extract_pin_table(pdf, [1])
        assert result == {"1": "MODE", "2": "VIN", "3": "SW", "4": "GND"}

    def test_subprocess_timeout(self, monkeypatch, tmp_path: Path) -> None:
        pdf = tmp_path / "test.pdf"
        pdf.write_text("dummy")

        import circuit_graph.knowledge.pdf_pin_extract as ppe

        def _fake_run(*args, **kwargs):
            import subprocess as _sp
            raise _sp.TimeoutExpired(cmd="camelot", timeout=30)

        monkeypatch.setattr(ppe.subprocess, "run", _fake_run)
        fake_venv = tmp_path / "fake_venv2"
        fake_venv.mkdir()
        (fake_venv / "Scripts").mkdir()
        (fake_venv / "Scripts" / "python.exe").write_text("")
        monkeypatch.setattr(ppe, "camelot_venv", lambda: fake_venv)

        result = extract_pin_table(pdf, [1])
        assert result is None

    def test_missing_pdf(self) -> None:
        result = extract_pin_table(Path("/nonexistent.pdf"), [1])
        assert result is None

    def test_empty_pages(self, tmp_path: Path) -> None:
        pdf = tmp_path / "test.pdf"
        pdf.write_text("dummy")
        result = extract_pin_table(pdf, [])
        assert result is None
