"""Place reference/value labels at 0° and resolve overlaps."""

from __future__ import annotations

from dataclasses import dataclass

from circuit_graph.ir import CircuitIR
from circuit_graph.layout.config import LabelConfig, LayoutConfig
from circuit_graph.layout.exceptions import LayoutOverlapError
from circuit_graph.layout.placer import Placement


@dataclass(frozen=True)
class Label:
    text: str
    x: float
    y: float
    angle: float
    kind: str  # "reference" or "value"


def place_labels(
    ir: CircuitIR,
    placements: dict[str, Placement],
    config: LayoutConfig,
) -> list[Label]:
    """Return labels for all components."""
    labels: list[Label] = []

    ref_cfg = config.labels.get("reference", LabelConfig())
    val_cfg = config.labels.get("value", LabelConfig())

    for comp in ir.components:
        placement = placements[comp.id]
        ref_text = comp.id
        val_text = comp.raw_value or comp.part_number or ""

        labels.append(_make_label(ref_text, placement, ref_cfg, "reference"))
        if val_text:
            labels.append(_make_label(val_text, placement, val_cfg, "value"))

    _resolve_overlaps(labels, config)
    return labels


def _make_label(text: str, placement: Placement, cfg: LabelConfig, kind: str) -> Label:
    x = placement.x + cfg.offset_mm[0]
    y = placement.y + cfg.offset_mm[1]
    return Label(text=text, x=x, y=y, angle=0.0, kind=kind)


def _resolve_overlaps(labels: list[Label], config: LayoutConfig) -> None:
    """Iteratively shift labels down until no overlaps remain.

    The algorithm repeats a full pairwise sweep until either no overlap is
    detected or ``max_sweeps`` is reached. This handles chains where moving
    one label creates a new overlap with a third label.
    """
    char = config.grid.char_unit_mm
    max_sweeps = 10
    total_attempts = 0
    max_total_attempts = 200

    for _ in range(max_sweeps):
        moved = False
        for i in range(len(labels)):
            for j in range(i + 1, len(labels)):
                if not _overlap(labels[i], labels[j], config):
                    continue
                if total_attempts >= max_total_attempts:
                    raise LayoutOverlapError(
                        f"cannot resolve overlap between {labels[i].text} and "
                        f"{labels[j].text}"
                    )
                # Shift the lower label down by one character unit.
                lower_idx = i if labels[i].y <= labels[j].y else j
                lower = labels[lower_idx]
                labels[lower_idx] = Label(
                    text=lower.text,
                    x=lower.x,
                    y=lower.y + char,
                    angle=lower.angle,
                    kind=lower.kind,
                )
                moved = True
                total_attempts += 1
        if not moved:
            return

    # If we exit the loop, at least one overlap remains.
    for i in range(len(labels)):
        for j in range(i + 1, len(labels)):
            if _overlap(labels[i], labels[j], config):
                raise LayoutOverlapError(
                    f"cannot resolve overlap between {labels[i].text} and "
                    f"{labels[j].text}"
                )


def _overlap(a: Label, b: Label, config: LayoutConfig) -> bool:
    """Simple AABB overlap using font height as box height and len*char as width."""
    char = config.grid.char_unit_mm
    h = char
    wa = len(a.text) * char
    wb = len(b.text) * char

    ax0, ax1 = a.x - wa / 2, a.x + wa / 2
    ay0, ay1 = a.y - h / 2, a.y + h / 2
    bx0, bx1 = b.x - wb / 2, b.x + wb / 2
    by0, by1 = b.y - h / 2, b.y + h / 2

    return ax0 < bx1 and ax1 > bx0 and ay0 < by1 and ay1 > by0
