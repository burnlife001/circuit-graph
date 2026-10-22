#!/usr/bin/env python3
"""
Native SPI decoder for ATK-Logic captures. Works from any directory.

Usage:
    python atk_spi.py <file.atkdl> --clk 0 [--mosi 1 --miso 2 --cs 3]
    python atk_spi.py <file.atkdl> --clk 0 --cpol 1 --cpha 1 --wordsize 16

Output:
    JSON list of frames: {"t_ns", "type", "data", "errors"}.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Support running from any directory — add parent (scripts/) to path
_parent_dir = Path(__file__).resolve().parent.parent
if str(_parent_dir) not in sys.path:
    sys.path.insert(0, str(_parent_dir))

from _proto_common import ProtoFrame, _EdgeHelper, normalize_options  # noqa: E402
from capture_reader import CaptureReader  # noqa: E402


class SpiDecoder:
    """SPI protocol decoder.

    Required channels:
        clk  — serial clock

    Optional channels:
        mosi — Master Out Slave In
        miso — Master In Slave Out
        cs   — Chip Select (active low)

    Optional options:
        wordsize — bits per word (default 8)
        bitorder — msb_first / lsb_first (default msb_first)
        cpol     — clock polarity: 0/1 (default 0)
        cpha     — clock phase: 0/1 (default 0)
        cs_polarity — active low/high (default low)
    """

    NATIVE_ID = "spi"

    @staticmethod
    def decoder_info():
        return {
            "id": "spi",
            "name": "SPI (native)",
            "desc": "Serial Peripheral Interface — MOSI/MISO/CS word decoder",
            "channels": [
                {"id": "clk", "name": "CLK", "desc": "Serial clock"},
            ],
            "opt_channels": [
                {"id": "mosi", "name": "MOSI", "desc": "Master Out Slave In"},
                {"id": "miso", "name": "MISO", "desc": "Master In Slave Out"},
                {"id": "cs", "name": "CS", "desc": "Chip Select"},
            ],
            "options": [
                {"id": "wordsize", "desc": "Bits per word (default 8)"},
                {"id": "bitorder", "desc": "msb_first/lsb_first (default msb_first)"},
                {"id": "cpol", "desc": "Clock polarity 0/1 (default 0)"},
                {"id": "cpha", "desc": "Clock phase 0/1 (default 0)"},
                {"id": "cs_polarity", "desc": "Chip select active low/high (default low)"},
            ],
        }

    def __init__(
        self,
        edges_clk: list,
        edges_mosi: list | None = None,
        edges_miso: list | None = None,
        edges_cs: list | None = None,
        sample_rate_hz: int = 0,
        options: dict | None = None,
    ):
        self._clk = _EdgeHelper(edges_clk)
        self._mosi = _EdgeHelper(edges_mosi) if edges_mosi else None
        self._miso = _EdgeHelper(edges_miso) if edges_miso else None
        self._cs = _EdgeHelper(edges_cs) if edges_cs else None
        opts = options or {}

        self.wordsize = int(opts.get("wordsize", 8))
        self.bitorder = opts.get("bitorder", "msb_first").lower()
        self.cpol = int(opts.get("cpol", 0))
        self.cpha = int(opts.get("cpha", 0))
        cs_pol = opts.get("cs_polarity", "low").lower()
        self._cs_active = 0 if cs_pol in ("low", "0") else 1

    def decode(self) -> list[ProtoFrame]:
        frames: list[ProtoFrame] = []
        clk_edges = self._clk.edges
        if len(clk_edges) < 2:
            return frames

        # Determine sampling edges based on CPOL/CPHA
        # CPHA=0: sample on first clock edge (leading) of each cycle
        # CPHA=1: sample on second clock edge (trailing) of each cycle
        # CPOL=0: idle low,  leading = rising,  trailing = falling
        # CPOL=1: idle high, leading = falling, trailing = rising
        if self.cpol == 0:
            leading_rising = True  # rising = 0→1
        else:
            leading_rising = False  # falling = 1→0

        if self.cpha == 0:
            sample_on_rising = leading_rising
        else:
            sample_on_rising = not leading_rising

        # Find sample edges
        sample_edges = []
        for i in range(1, len(clk_edges)):
            prev, cur = clk_edges[i - 1], clk_edges[i]
            is_rising = prev.level == 0 and cur.level == 1
            if is_rising == sample_on_rising:
                sample_edges.append(cur.t_ns)

        if not sample_edges:
            return frames

        # Group by CS if available
        cs_regions = self._find_cs_regions(sample_edges[0], sample_edges[-1])

        if cs_regions:
            for cs_start, cs_end in cs_regions:
                frames.append(ProtoFrame(
                    t_ns=cs_start,
                    type="cs_assert",
                    data={"text": "CS asserted"},
                ))

                region_samples = [t for t in sample_edges if cs_start <= t < cs_end]
                self._decode_words(region_samples, frames)

                frames.append(ProtoFrame(
                    t_ns=cs_end,
                    type="cs_deassert",
                    data={"text": "CS deasserted"},
                ))
        else:
            self._decode_words(sample_edges, frames)

        return frames

    def _decode_words(self, sample_times: list[int], frames: list[ProtoFrame]) -> None:
        """Decode words from a list of sample times."""
        word_count = 0
        for wi in range(0, len(sample_times), self.wordsize):
            chunk = sample_times[wi : wi + self.wordsize]
            if len(chunk) < self.wordsize:
                break

            mosi_byte = 0
            miso_byte = 0
            bits_data = []

            for bi, t_ns in enumerate(chunk):
                bit_pos = bi if self.bitorder == "msb_first" else (self.wordsize - 1 - bi)

                mosi_bit = self._mosi.level_at(t_ns) if self._mosi else 0
                miso_bit = self._miso.level_at(t_ns) if self._miso else 0
                mosi_byte |= mosi_bit << (self.wordsize - 1 - bit_pos)
                miso_byte |= miso_bit << (self.wordsize - 1 - bit_pos)

                bits_data.append({
                    "mosi": mosi_bit,
                    "miso": miso_bit,
                })

                frames.append(ProtoFrame(
                    t_ns=t_ns,
                    type="bit",
                    data={"word": word_count, "bit": bi, "mosi": mosi_bit, "miso": miso_bit},
                ))

            mosi_text = chr(mosi_byte) if 32 <= mosi_byte < 127 else f"[{mosi_byte:02X}]"
            miso_text = chr(miso_byte) if 32 <= miso_byte < 127 else f"[{miso_byte:02X}]"
            frames.append(ProtoFrame(
                t_ns=chunk[0],
                type="data",
                data={
                    "word": word_count,
                    "mosi_hex": f"0x{mosi_byte:02X}",
                    "miso_hex": f"0x{miso_byte:02X}",
                    "mosi_text": mosi_text,
                    "miso_text": miso_text,
                },
            ))
            word_count += 1

    def _find_cs_regions(self, t_start: int, t_end: int) -> list[tuple[int, int]]:
        """Find CS-asserted time regions."""
        if not self._cs:
            return []

        cs_edges = self._cs.edges
        regions = []
        in_active = False
        active_start = 0

        for i in range(len(cs_edges)):
            e = cs_edges[i]
            if e.t_ns > t_end:
                break
            if e.level == self._cs_active and not in_active:
                in_active = True
                active_start = e.t_ns
            elif e.level != self._cs_active and in_active:
                in_active = False
                end_ns = e.t_ns
                if end_ns > t_start:
                    regions.append((max(active_start, t_start), end_ns))

        if in_active:
            regions.append((max(active_start, t_start), t_end))

        return regions


def main():
    ap = argparse.ArgumentParser(description="Native SPI decoder for ATK-Logic captures")
    ap.add_argument("file", help="Path to .atkdl or .bin file")
    ap.add_argument("--clk", type=int, required=True, help="CLK channel ID")
    ap.add_argument("--mosi", type=int, default=None, help="MOSI channel ID")
    ap.add_argument("--miso", type=int, default=None, help="MISO channel ID")
    ap.add_argument("--cs", type=int, default=None, help="CS channel ID")
    ap.add_argument("--wordsize", type=int, default=8)
    ap.add_argument("--bitorder", default="msb_first")
    ap.add_argument("--cpol", type=int, default=0)
    ap.add_argument("--cpha", type=int, default=0)
    ap.add_argument("--cs-polarity", default="low")
    ap.add_argument("--start", default="0", help="Start time (ns/us/ms/s)")
    ap.add_argument("--end", default=None, help="End time (ns/us/ms/s)")
    args = ap.parse_args()

    from capture_reader import parse_time
    start_ns = parse_time(args.start)
    end_ns = parse_time(args.end) if args.end else None

    reader = CaptureReader(args.file)

    def _read(ch):
        return reader.read_edges(ch, start_ns, end_ns) if ch is not None else None

    opts = normalize_options(vars(args))
    dec = SpiDecoder(
        _read(args.clk), _read(args.mosi), _read(args.miso), _read(args.cs),
        reader.get_info().sample_rate_hz, opts,
    )
    frames = dec.decode()

    print(json.dumps(
        [{"t_ns": f.t_ns, "type": f.type, "data": f.data, "errors": f.errors}
         for f in frames], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
