#!/usr/bin/env python3
"""
Native UART decoder for ATK-Logic captures. Works from any directory.

Usage:
    python atk_uart.py <file.atkdl> --rx 4 [--baudrate 115200]
    python atk_uart.py <file.atkdl> --tx 3 --baudrate 9600 --parity even

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

from _proto_common import ProtoFrame, _EdgeHelper, normalize_options
from capture_reader import CaptureReader


class UartDecoder:
    """Asynchronous serial decoder.

    Required options:
        baudrate  — symbol rate (default 115200)

    Optional options:
        data_bits — 5–9 (default 8)
        parity    — none / even / odd / mark / space (default none)
        stop_bits — 1 / 1.5 / 2 (default 1)
        bit_order — lsb_first / msb_first (default lsb_first)
        polarity  — 0 = idle high (default), 1 = idle low
    """

    NATIVE_ID = "uart"

    @staticmethod
    def decoder_info():
        return {
            "id": "uart",
            "name": "UART (native)",
            "desc": "Asynchronous serial — start/data/parity/stop frame decoder",
            "channels": [],
            "opt_channels": [
                {"id": "rx", "name": "RX", "desc": "Receive data"},
                {"id": "tx", "name": "TX", "desc": "Transmit data"},
            ],
            "options": [
                {"id": "baudrate", "desc": "Symbol rate (default 115200)"},
                {"id": "data_bits", "desc": "Data bits: 5-9 (default 8)"},
                {"id": "parity", "desc": "none/even/odd/mark/space (default none)"},
                {"id": "stop_bits", "desc": "Stop bits: 1/1.5/2 (default 1)"},
                {"id": "bit_order", "desc": "lsb_first/msb_first (default lsb_first)"},
            ],
        }

    def __init__(
        self,
        edges: list,
        sample_rate_hz: int,
        options: dict | None = None,
    ):
        self._rx = _EdgeHelper(edges)
        self._sample_rate_hz = sample_rate_hz
        opts = options or {}

        self.baudrate = int(opts.get("baudrate", 115200))
        self.data_bits = int(opts.get("data_bits", 8))
        self.stop_bits_val = float(opts.get("stop_bits", 1))
        self.parity = opts.get("parity", "none").lower()
        self.bit_order = opts.get("bit_order", "lsb_first").lower()
        self.polarity = int(opts.get("polarity", 0))  # 0 = idle high

        self._bit_ns = 1_000_000_000 // self.baudrate
        self._sample_offset_ns = self._bit_ns // 2  # sample at bit center

        self._idle_level = 1 if self.polarity == 0 else 0
        self._active_level = 1 - self._idle_level

    def decode(self, drop_errors: bool = True) -> list[ProtoFrame]:
        frames: list[ProtoFrame] = []
        edges = self._rx.edges
        if len(edges) < 2:
            return frames

        # Find start bits: idle→active transition = start of frame
        i = 0
        while i < len(edges) - 1:
            prev, cur = edges[i], edges[i + 1]
            i += 1
            if prev.level != self._idle_level or cur.level != self._active_level:
                continue

            start_ns = cur.t_ns
            frame = self._decode_one_frame(start_ns)
            if drop_errors and any(f.errors for f in frame):
                pass
            else:
                frames.extend(frame)

            # Skip past this frame: advance i to frame_end so the next
            # iteration starts looking AFTER the stop bit.
            frame_end = start_ns + self._frame_ns()
            while i < len(edges) and edges[i].t_ns < frame_end:
                i += 1
            # After skipping, if we landed on an active edge, back up one
            # edge so the idle→active pair is visible to the next iteration.
            if i < len(edges) and edges[i].level == self._active_level:
                i -= 1

        return frames

    def _frame_ns(self) -> int:
        """Total frame duration in ns: 1 start + data + parity + stop."""
        bits = 1 + self.data_bits + (0 if self.parity == "none" else 1) + self.stop_bits_val
        return int(bits * self._bit_ns)

    def _decode_one_frame(self, start_ns: int) -> list[ProtoFrame]:
        """Decode a single UART frame starting at start_ns."""
        frames: list[ProtoFrame] = []

        frames.append(ProtoFrame(
            t_ns=start_ns,
            type="start",
            data={"text": "Start"},
        ))

        # Sample data bits at bit centers
        bit_values = []
        for bi in range(self.data_bits):
            sample_ns = start_ns + self._bit_ns + bi * self._bit_ns + self._sample_offset_ns
            level = self._rx.level_at(sample_ns)
            # Standard UART: line low=bit 0, line high=bit 1.
            # Inverted (polarity=1): line low=bit 1, line high=bit 0.
            bit = level if self._idle_level == 1 else 1 - level
            bit_values.append(bit)
            frames.append(ProtoFrame(
                t_ns=sample_ns,
                type="bit",
                data={"bit": bi, "value": bit},
            ))

        # Assemble byte
        if self.bit_order == "lsb_first":
            byte_val = sum(b << i for i, b in enumerate(bit_values))
        else:
            byte_val = sum(b << (self.data_bits - 1 - i) for i, b in enumerate(bit_values))

        # Parity
        parity_error = False
        parity_ns = start_ns + self._bit_ns + self.data_bits * self._bit_ns
        if self.parity != "none":
            parity_ns += self._sample_offset_ns
            parity_level = self._rx.level_at(int(parity_ns))
            parity_bit = parity_level if self._idle_level == 1 else 1 - parity_level

            expected = self._expected_parity(byte_val)
            if parity_bit != expected:
                parity_error = True

            frames.append(ProtoFrame(
                t_ns=int(parity_ns),
                type="parity",
                data={"value": parity_bit, "expected": expected},
                errors=["Parity error"] if parity_error else [],
            ))
            parity_ns += self._bit_ns

        # Stop bit(s)
        stop_errors = []
        for sb in range(int(self.stop_bits_val)):
            stop_ns = int(parity_ns + sb * self._bit_ns + self._sample_offset_ns)
            stop_level = self._rx.level_at(stop_ns)
            if stop_level != self._idle_level:
                stop_errors.append(f"Stop bit {sb + 1} error")
            frames.append(ProtoFrame(
                t_ns=stop_ns,
                type="stop",
                data={"bit": sb, "value": 1 if stop_level == self._idle_level else 0},
                errors=stop_errors[-1:] if stop_level != self._idle_level else [],
            ))

        texts = [chr(byte_val) if 32 <= byte_val < 127 else f"[{byte_val:02X}]"]
        frame_data = {
            "byte": byte_val,
            "hex": f"0x{byte_val:02X}",
            "text": texts[0],
            "ascii": chr(byte_val) if 32 <= byte_val < 127 else None,
        }

        all_errors = []
        if parity_error:
            all_errors.append("Parity error")
        all_errors.extend(stop_errors)

        frames.append(ProtoFrame(
            t_ns=start_ns,
            type="data",
            data=frame_data,
            errors=all_errors,
        ))

        return frames

    def _expected_parity(self, byte_val: int) -> int:
        bits = bin(byte_val).count("1")
        if self.parity == "even":
            return bits % 2
        elif self.parity == "odd":
            return 1 - (bits % 2)
        elif self.parity == "mark":
            return 1
        elif self.parity == "space":
            return 0
        return 0


def main():
    ap = argparse.ArgumentParser(description="Native UART decoder for ATK-Logic captures")
    ap.add_argument("file", help="Path to .atkdl or .bin file")
    ap.add_argument("--rx", type=int, default=None, help="RX channel ID")
    ap.add_argument("--tx", type=int, default=None, help="TX channel ID")
    ap.add_argument("--baudrate", type=int, default=115200)
    ap.add_argument("--data-bits", type=int, default=8)
    ap.add_argument("--parity", default="none")
    ap.add_argument("--stop-bits", type=float, default=1)
    ap.add_argument("--bit-order", default="lsb_first")
    ap.add_argument("--start", default="0", help="Start time (ns/us/ms/s)")
    ap.add_argument("--end", default=None, help="End time (ns/us/ms/s)")
    args = ap.parse_args()

    if args.rx is None and args.tx is None:
        ap.error("UART requires --rx or --tx")

    from capture_reader import parse_time
    start_ns = parse_time(args.start)
    end_ns = parse_time(args.end) if args.end else None

    reader = CaptureReader(args.file)
    edges = reader.read_edges(args.rx if args.rx is not None else args.tx,
                              start_ns, end_ns)
    opts = normalize_options(vars(args))
    dec = UartDecoder(edges, reader.get_info().sample_rate_hz, opts)
    frames = dec.decode()

    print(json.dumps(
        [{"t_ns": f.t_ns, "type": f.type, "data": f.data, "errors": f.errors}
         for f in frames], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
