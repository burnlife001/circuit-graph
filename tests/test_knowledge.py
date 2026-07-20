"""Tests for the component part-number knowledge base."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from circuit_graph.knowledge import KnowledgeBase

DB_PATH = Path(__file__).resolve().parents[1] / "circuit_graph" / "knowledge" / "vendor_db.json"


@pytest.fixture
def kb() -> KnowledgeBase:
    return KnowledgeBase.load(DB_PATH)


def test_load_returns_queryable_object(kb: KnowledgeBase) -> None:
    # Load succeeds and at least the four required prefixes are present.
    assert kb is not None
    prefixes = kb.known_prefixes()
    for required in ("JW", "HT", "PE", "TP"):
        assert required in prefixes, f"missing prefix in DB: {required}"


def test_entry_shape_for_jw(kb: KnowledgeBase) -> None:
    entry = kb.lookup_prefix("JW5065")
    assert entry is not None
    # Spec requires manufacturer / category / typical_topology fields.
    assert entry["manufacturer"] == "Joulwatt"
    assert entry["category"] == "dcdc"
    assert entry["typical_topology"] == "synchronous_buck"


def test_lookup_prefix_is_prefix_based(kb: KnowledgeBase) -> None:
    # Different part numbers sharing a prefix resolve to the same entry.
    jw1 = kb.lookup_prefix("JW5065")
    jw2 = kb.lookup_prefix("JW1234")
    assert jw1 is not None and jw2 is not None
    assert jw1["category"] == jw2["category"]


def test_lookup_prefix_unknown_returns_none(kb: KnowledgeBase) -> None:
    # Spec: unknown prefix returns None (not an exception).
    assert kb.lookup_prefix("UNKNOWN123") is None
    assert kb.get_category("UNKNOWN123") is None


def test_lookup_prefix_case_insensitive(kb: KnowledgeBase) -> None:
    # Lowercase part numbers should still find their uppercase prefix.
    assert kb.lookup_prefix("jw5065") is not None
    assert kb.lookup_prefix("Jw5065") is not None


def test_lookup_prefix_handles_non_alpha_chars(kb: KnowledgeBase) -> None:
    # Real part numbers sometimes include '-' or '/' suffixes; the prefix
    # extractor should ignore everything after the first non-letter.
    assert kb.lookup_prefix("HT7533-1") is not None
    assert kb.lookup_prefix("PE8205A") is not None


def test_get_category(kb: KnowledgeBase) -> None:
    assert kb.get_category("JW5065") == "dcdc"
    assert kb.get_category("HT7533") == "ldo"
    assert kb.get_category("PE8205") == "mosfet_pair"
    assert kb.get_category("TP1234") == "touch_ic"


def test_is_dcdc(kb: KnowledgeBase) -> None:
    assert kb.is_dcdc("JW5065") is True
    assert kb.is_dcdc("HT7533") is False
    assert kb.is_dcdc("UNKNOWN999") is False


def test_is_ldo(kb: KnowledgeBase) -> None:
    assert kb.is_ldo("HT7533") is True
    assert kb.is_ldo("JW5065") is False
    assert kb.is_ldo("UNKNOWN999") is False


def test_is_bridge_driver_never_true_for_seed_data(kb: KnowledgeBase) -> None:
    # ``bridge_driver`` is not yet in the seed data; the helper must not lie.
    assert kb.is_bridge_driver("PE8205") is False
    assert kb.is_bridge_driver("JW5065") is False


def test_longest_prefix_match(tmp_path: Path) -> None:
    # When multiple prefixes could match, prefer the longest. This guards
    # against the loader accidentally collapsing ``JW50`` and ``J``.
    db = {
        "entries": {
            "J": {"manufacturer": "J", "category": "generic"},
            "JW50": {"manufacturer": "Joulwatt50", "category": "dcdc"},
        }
    }
    p = tmp_path / "kb.json"
    p.write_text(json.dumps(db), encoding="utf-8")
    kb = KnowledgeBase.load(p)
    assert kb.lookup_prefix("JW5065")["manufacturer"] == "Joulwatt50"


def test_reload_refreshes_after_edit(tmp_path: Path) -> None:
    # Edit the JSON file on disk and confirm ``reload`` picks the change up.
    p = tmp_path / "kb.json"
    p.write_text(
        json.dumps(
            {
                "entries": {
                    "XX": {"manufacturer": "X", "category": "ldo"},
                }
            }
        ),
        encoding="utf-8",
    )
    kb = KnowledgeBase.load(p)
    assert kb.is_ldo("XX1") is True

    p.write_text(
        json.dumps(
            {
                "entries": {
                    "XX": {"manufacturer": "X", "category": "dcdc"},
                }
            }
        ),
        encoding="utf-8",
    )
    kb.reload()
    assert kb.is_ldo("XX1") is False
    assert kb.is_dcdc("XX1") is True


def test_load_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        KnowledgeBase.load(tmp_path / "does-not-exist.json")


def test_load_malformed_json_raises(tmp_path: Path) -> None:
    p = tmp_path / "kb.json"
    p.write_text("not-json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        KnowledgeBase.load(p)
