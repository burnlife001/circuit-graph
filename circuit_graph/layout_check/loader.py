"""Load .schlayout YAML rule files into RuleSpec[] with strict schema validation.

All 6 error kinds from the spec section 6.2 / 7 are exposed via
:class:`LayoutCheckError`:

- ``rules_file_not_found``     -- path does not exist
- ``yaml_parse_error``         -- PyYAML YAMLError, malformed YAML, or wrong type
- ``schema_version_mismatch``  -- schema_version missing or != 1
- ``unknown_assert_kind``      -- assert.kind not in BUILT_IN_ASSERTION_KINDS
- ``duplicate_rule_id``        -- two rules share the same id
- ``schematic_parse_error``    -- set by parser layer (NOT used here, kept for
                                  completeness so the orchestrator can re-raise
                                  under a single error union).

The loader is read-only; consumers iterate over RuleSpec and decide what to run.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from circuit_graph.layout_check.model import (
    BUILT_IN_ASSERTION_KINDS,
    AppliesTo,
    AssertSpec,
    RuleSpec,
)

# Allowed keys inside a rule's ``applies_to`` block. Any other key raises a
# ``yaml_parse_error`` (the spec does not define a separate "structural" kind).
_APPLIES_TO_KEYS: tuple[str, ...] = ("refdes_prefix", "type", "net", "kind")


class LayoutCheckError(Exception):
    """Raised on configuration / parse errors during ``cg layout-check``.

    Attributes
    ----------
    kind : str
        One of the 6 documented error kinds. Stable for the v1 contract.
    message : str
        Human-readable message.
    location : str
        Where in the file the error occurred (``"file:line:col"`` or ``"file"``).
    cause_path : str
        JSON-pointer-like path to the offending YAML node
        (``"$.rules[3].assert.kind"``).
    """

    def __init__(
        self,
        kind: str,
        message: str,
        location: str,
        cause_path: str,
    ) -> None:
        self.kind = kind
        self.message = message
        self.location = location
        self.cause_path = cause_path
        super().__init__(message)


def _yaml_location(path: Path, err: yaml.YAMLError | None = None) -> str:
    """Return a best-effort ``path:line:col`` string from a PyYAML error."""
    if err is not None:
        mark = getattr(err, "problem_mark", None)
        if mark is not None:
            return f"{path}:{mark.line + 1}:{mark.column + 1}"
    return str(path)


def load_rules(path: Path) -> list[RuleSpec]:
    """Load ``.schlayout`` from ``path`` and return a list of :class:`RuleSpec`.

    On any error, raises :class:`LayoutCheckError`. On success returns the rule
    list (may be empty if the file has ``rules: []``).

    Validation steps (in order):

    1. File exists -- else ``rules_file_not_found``.
    2. YAML parses -- else ``yaml_parse_error``.
    3. Top-level mapping has ``schema_version`` == 1 -- else
       ``schema_version_mismatch``.
    4. ``rules`` is a list -- else ``yaml_parse_error``.
    5. Per rule: id (non-empty str), enabled (bool, default True), optional
       ``applies_to`` (mapping with the 4 known keys), required ``assert``
       mapping with ``kind`` in BUILT_IN_ASSERTION_KINDS.
    6. Per file: ids are unique -- else ``duplicate_rule_id``.
    """
    path = Path(path)

    # 1. Existence check (done before reading so the error is precise).
    if not path.exists():
        raise LayoutCheckError(
            kind="rules_file_not_found",
            message=f"rules file not found: {path}",
            location=str(path),
            cause_path="$",
        )

    # 2. YAML parse.
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise LayoutCheckError(
            kind="yaml_parse_error",
            message=f"YAML parse error: {err}",
            location=_yaml_location(path, err),
            cause_path="$",
        ) from err

    if not isinstance(raw, dict):
        raise LayoutCheckError(
            kind="yaml_parse_error",
            message=f"top-level YAML must be a mapping, got {type(raw).__name__}",
            location=str(path),
            cause_path="$",
        )

    # 3. schema_version check.
    schema_version = raw.get("schema_version")
    if schema_version != 1:
        raise LayoutCheckError(
            kind="schema_version_mismatch",
            message=f"schema_version must be integer 1, got {schema_version!r}",
            location=str(path),
            cause_path="$.schema_version",
        )

    # 4. rules must be a list.
    rules_raw = raw.get("rules")
    if not isinstance(rules_raw, list):
        raise LayoutCheckError(
            kind="yaml_parse_error",
            message=f"top-level 'rules' must be a list, got {type(rules_raw).__name__}",
            location=str(path),
            cause_path="$.rules",
        )

    # 5. Per-rule structural validation.
    parsed: list[RuleSpec] = []
    for idx, rule_raw in enumerate(rules_raw):
        parsed.append(_parse_rule(rule_raw, idx, path))

    # 6. Id uniqueness across the whole file.
    seen: set[str] = set()
    for idx, rule in enumerate(parsed):
        if rule.id in seen:
            raise LayoutCheckError(
                kind="duplicate_rule_id",
                message=f"duplicate rule id {rule.id!r}",
                location=str(path),
                cause_path=f"$.rules[{idx}].id",
            )
        seen.add(rule.id)

    return parsed


def _parse_rule(rule_raw: Any, idx: int, path: Path) -> RuleSpec:
    """Validate one rule dict and produce a :class:`RuleSpec`."""
    cp = f"$.rules[{idx}]"

    if not isinstance(rule_raw, dict):
        raise LayoutCheckError(
            kind="yaml_parse_error",
            message=f"rule must be a mapping, got {type(rule_raw).__name__}",
            location=str(path),
            cause_path=cp,
        )

    rule_id = rule_raw.get("id")
    if not isinstance(rule_id, str) or rule_id == "":
        raise LayoutCheckError(
            kind="yaml_parse_error",
            message=f"rule.id must be a non-empty string, got {rule_id!r}",
            location=str(path),
            cause_path=f"{cp}.id",
        )

    enabled_raw = rule_raw.get("enabled", True)
    if not isinstance(enabled_raw, bool):
        raise LayoutCheckError(
            kind="yaml_parse_error",
            message=f"rule.enabled must be a bool, got {type(enabled_raw).__name__}",
            location=str(path),
            cause_path=f"{cp}.enabled",
        )

    applies_to = _parse_applies_to(
        rule_raw.get("applies_to"), f"{cp}.applies_to", path
    )
    assertion = _parse_assert(rule_raw.get("assert"), f"{cp}.assert", path)

    return RuleSpec(
        id=rule_id,
        enabled=enabled_raw,
        applies_to=applies_to,
        assertion=assertion,
    )


def _parse_applies_to(
    raw: Any,
    cause_path: str,
    path: Path,
) -> AppliesTo | None:
    """Build :class:`AppliesTo` from YAML; absent key returns ``None``."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise LayoutCheckError(
            kind="yaml_parse_error",
            message=f"applies_to must be a mapping, got {type(raw).__name__}",
            location=str(path),
            cause_path=cause_path,
        )

    unknown = set(raw.keys()) - set(_APPLIES_TO_KEYS)
    if unknown:
        raise LayoutCheckError(
            kind="yaml_parse_error",
            message=(
                f"applies_to has unknown keys {sorted(unknown)!r}; "
                f"allowed: {list(_APPLIES_TO_KEYS)}"
            ),
            location=str(path),
            cause_path=cause_path,
        )

    kwargs: dict[str, list[str]] = {}
    for key in _APPLIES_TO_KEYS:
        val = raw.get(key, [])
        if not isinstance(val, list) or not all(isinstance(x, str) for x in val):
            raise LayoutCheckError(
                kind="yaml_parse_error",
                message=f"applies_to.{key} must be a list[str], got {val!r}",
                location=str(path),
                cause_path=f"{cause_path}.{key}",
            )
        kwargs[key] = val
    return AppliesTo(**kwargs)


def _parse_assert(raw: Any, cause_path: str, path: Path) -> AssertSpec:
    """Build :class:`AssertSpec` from the ``assert:`` YAML block."""
    if not isinstance(raw, dict):
        raise LayoutCheckError(
            kind="yaml_parse_error",
            message=f"assert must be a mapping, got {type(raw).__name__}",
            location=str(path),
            cause_path=cause_path,
        )

    kind = raw.get("kind")
    if not isinstance(kind, str):
        raise LayoutCheckError(
            kind="yaml_parse_error",
            message=f"assert.kind must be a string, got {kind!r}",
            location=str(path),
            cause_path=f"{cause_path}.kind",
        )

    if kind not in BUILT_IN_ASSERTION_KINDS:
        raise LayoutCheckError(
            kind="unknown_assert_kind",
            message=(
                f"unknown assert.kind {kind!r}; "
                f"allowed: {sorted(BUILT_IN_ASSERTION_KINDS)}"
            ),
            location=str(path),
            cause_path=f"{cause_path}.kind",
        )

    # All keys except ``kind`` are treated as assertion-specific parameters.
    params = {k: v for k, v in raw.items() if k != "kind"}
    return AssertSpec(kind=kind, params=params)
