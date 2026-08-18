"""Tests for circuit_graph.layout_check.loader."""

from __future__ import annotations

import textwrap

import pytest

from circuit_graph.layout_check.loader import LayoutCheckError, load_rules


def test_happy_path_loads_all_fields(tmp_path):
    p = tmp_path / "ok.schlayout"
    p.write_text(
        textwrap.dedent(
            """
            schema_version: 1
            rules:
              - id: r1
                enabled: true
                applies_to:
                  refdes_prefix: ["R*"]
                  type: ["resistor"]
                  net: ["5V*"]
                  kind: ["power"]
                assert:
                  kind: designator-present
              - id: r2
                enabled: false
                assert:
                  kind: value-present
                  forbidden_default: ["NC", "0"]
            """
        ),
        encoding="utf-8",
    )
    rules = load_rules(p)
    assert len(rules) == 2
    assert rules[0].id == "r1"
    assert rules[0].enabled is True
    assert rules[0].applies_to is not None
    assert rules[0].applies_to.refdes_prefix == ["R*"]
    assert rules[0].applies_to.type == ["resistor"]
    assert rules[0].applies_to.net == ["5V*"]
    assert rules[0].applies_to.kind == ["power"]
    assert rules[0].assertion.kind == "designator-present"
    assert rules[0].assertion.params == {}

    assert rules[1].id == "r2"
    assert rules[1].enabled is False
    assert rules[1].applies_to is None
    assert rules[1].assertion.kind == "value-present"
    assert rules[1].assertion.params == {"forbidden_default": ["NC", "0"]}


def test_missing_schema_version(tmp_path):
    p = tmp_path / "bad.schlayout"
    p.write_text(
        textwrap.dedent(
            """
            rules:
              - id: r1
                assert: {kind: designator-present}
            """
        ).strip(),
        encoding="utf-8",
    )
    with pytest.raises(LayoutCheckError) as ei:
        load_rules(p)
    assert ei.value.kind == "schema_version_mismatch"


def test_wrong_schema_version(tmp_path):
    p = tmp_path / "bad.schlayout"
    p.write_text(
        textwrap.dedent(
            """
            schema_version: 2
            rules:
              - id: r1
                assert: {kind: designator-present}
            """
        ).strip(),
        encoding="utf-8",
    )
    with pytest.raises(LayoutCheckError) as ei:
        load_rules(p)
    assert ei.value.kind == "schema_version_mismatch"


def test_duplicate_rule_id(tmp_path):
    p = tmp_path / "dup.schlayout"
    p.write_text(
        textwrap.dedent(
            """
            schema_version: 1
            rules:
              - id: same
                assert: {kind: designator-present}
              - id: same
                assert: {kind: value-present}
            """
        ).strip(),
        encoding="utf-8",
    )
    with pytest.raises(LayoutCheckError) as ei:
        load_rules(p)
    assert ei.value.kind == "duplicate_rule_id"


def test_unknown_assert_kind(tmp_path):
    p = tmp_path / "bad.schlayout"
    p.write_text(
        textwrap.dedent(
            """
            schema_version: 1
            rules:
              - id: r1
                assert:
                  kind: not-a-real-kind
            """
        ).strip(),
        encoding="utf-8",
    )
    with pytest.raises(LayoutCheckError) as ei:
        load_rules(p)
    assert ei.value.kind == "unknown_assert_kind"


def test_enabled_false_is_returned(tmp_path):
    p = tmp_path / "rule.schlayout"
    p.write_text(
        textwrap.dedent(
            """
            schema_version: 1
            rules:
              - id: r1
                enabled: false
                assert: {kind: designator-present}
            """
        ).strip(),
        encoding="utf-8",
    )
    rules = load_rules(p)
    assert len(rules) == 1
    assert rules[0].id == "r1"
    assert rules[0].enabled is False


def test_empty_rules_list(tmp_path):
    p = tmp_path / "empty.schlayout"
    p.write_text("schema_version: 1\nrules: []\n", encoding="utf-8")
    assert load_rules(p) == []


def test_yaml_parse_error(tmp_path):
    p = tmp_path / "bad.schlayout"
    p.write_text("schema_version: 1\nrules: [unclosed", encoding="utf-8")
    with pytest.raises(LayoutCheckError) as ei:
        load_rules(p)
    assert ei.value.kind == "yaml_parse_error"


def test_nonexistent_file_raises(tmp_path):
    p = tmp_path / "nope.schlayout"
    with pytest.raises(LayoutCheckError) as ei:
        load_rules(p)
    assert ei.value.kind == "rules_file_not_found"
