from pathlib import Path

import pytest

from circuit_graph.layout.config import LayoutConfig, load_layout_config
from circuit_graph.layout.exceptions import LayoutConfigError


def test_default_config_loads():
    cfg = load_layout_config(None)
    assert cfg.grid.size_mm == 2.54
    assert cfg.placement.input_x_mm == 38.1


def test_config_from_dict():
    cfg = LayoutConfig.from_dict(
        {
            "grid": {"size_mm": 1.27, "char_unit_mm": 1.0},
            "symbols": {
                "Device:R": {"rotation": 90, "pin_directions": {"1": "up", "2": "down"}}
            },
        }
    )
    assert cfg.grid.size_mm == 1.27
    assert cfg.symbols["Device:R"].rotation == 90


def test_config_rejects_non_dict():
    with pytest.raises(LayoutConfigError):
        LayoutConfig.from_dict("not-a-dict")


def test_config_rejects_unknown_top_level_key():
    with pytest.raises(LayoutConfigError, match="unknown top-level"):
        LayoutConfig.from_dict({"grid": {"size_mm": 2.54}, "typo": 1})


def test_config_file_round_trip(tmp_path: Path):
    path = tmp_path / "cfg.json"
    path.write_text('{"grid": {"size_mm": 5.08}}', encoding="utf-8")
    cfg = load_layout_config(path)
    assert cfg.grid.size_mm == 5.08
