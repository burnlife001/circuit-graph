"""
Shared building blocks for the native ATK-Logic protocol decoders
(atk_uart.py / atk_i2c.py / atk_spi.py).

Contains ProtoFrame (decoder output record), _EdgeHelper (fast
level-at-time queries on RLE edge lists) and normalize_options
(CLI option cleanup).
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field


@dataclass
class ProtoFrame:
    t_ns: int
    type: str              # "data" | "start" | "stop" | "ack" | "nack" | "address"
    data: dict             # protocol-specific payload, e.g. {"byte": 65, "text": "A"}
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Edge helper: fast level-at-time query via binary search on RLE edges
# ---------------------------------------------------------------------------

class _EdgeHelper:
    """Wraps a sorted list of EdgeEvent for O(log n) level-at-time queries."""

    def __init__(self, edges: list):
        self._edges = edges
        self._starts = [e.t_ns for e in edges]

    def level_at(self, t_ns: int) -> int:
        if not self._edges:
            return 0
        idx = bisect.bisect_right(self._starts, t_ns) - 1
        if idx < 0:
            idx = 0
        return self._edges[idx].level

    def rising_edges(self, t_start: int = 0, t_end: int | None = None) -> list[int]:
        """Return times of rising edges (0→1 transitions) in [t_start, t_end)."""
        result = []
        for i in range(1, len(self._edges)):
            prev, cur = self._edges[i - 1], self._edges[i]
            if prev.level == 0 and cur.level == 1:
                if cur.t_ns < t_start:
                    continue
                if t_end is not None and cur.t_ns >= t_end:
                    break
                result.append(cur.t_ns)
        return result

    def falling_edges(self, t_start: int = 0, t_end: int | None = None) -> list[int]:
        """Return times of falling edges (1→0 transitions) in [t_start, t_end)."""
        result = []
        for i in range(1, len(self._edges)):
            prev, cur = self._edges[i - 1], self._edges[i]
            if prev.level == 1 and cur.level == 0:
                if cur.t_ns < t_start:
                    continue
                if t_end is not None and cur.t_ns >= t_end:
                    break
                result.append(cur.t_ns)
        return result

    @property
    def edges(self) -> list:
        return self._edges


def normalize_options(opts: dict) -> dict:
    """Drop None values so decoder defaults apply; pass everything else through."""
    return {k: v for k, v in opts.items() if v is not None}
