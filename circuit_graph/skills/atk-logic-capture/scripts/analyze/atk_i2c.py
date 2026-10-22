#!/usr/bin/env python3
"""
Native I²C decoder for ATK-Logic captures. Works from any directory.

Usage:
    python atk_i2c.py <file.atkdl> --scl 0 --sda 1 [--addressing 7bit]

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


class I2CDecoder:
    """I²C / TWI protocol decoder.

    Required channels:
        scl — serial clock
        sda — serial data

    Optional options:
        addressing — 7bit (default) / 10bit
    """

    NATIVE_ID = "i2c"

    @staticmethod
    def decoder_info():
        return {
            "id": "i2c",
            "name": "I²C (native)",
            "desc": "Inter-Integrated Circuit — START/STOP/address/data/ACK decoder",
            "channels": [
                {"id": "scl", "name": "SCL", "desc": "Serial clock"},
                {"id": "sda", "name": "SDA", "desc": "Serial data"},
            ],
            "opt_channels": [],
            "options": [
                {"id": "addressing", "desc": "Addressing mode: 7bit (default) / 10bit"},
            ],
        }

    # I²C state machine
    _IDLE = 0
    _ADDRESS = 1
    _DATA = 2

    def __init__(
        self,
        edges_scl: list,
        edges_sda: list,
        sample_rate_hz: int,
        options: dict | None = None,
    ):
        self._scl = _EdgeHelper(edges_scl)
        self._sda = _EdgeHelper(edges_sda)
        self._sample_rate_hz = sample_rate_hz
        opts = options or {}
        self._addressing = opts.get("addressing", "7bit").lower()

    def decode(self) -> list[ProtoFrame]:
        frames: list[ProtoFrame] = []
        scl_edges = self._scl.edges
        sda_edges = self._sda.edges
        if len(scl_edges) < 2 or len(sda_edges) < 2:
            return frames

        # Find all START and STOP conditions first
        # START: SDA↓ while SCL=1
        # STOP:  SDA↑ while SCL=1
        starts = []
        stops = []
        for i in range(1, len(sda_edges)):
            prev, cur = sda_edges[i - 1], sda_edges[i]
            scl_level = self._scl.level_at(cur.t_ns)
            if scl_level != 1:
                continue
            if prev.level == 1 and cur.level == 0:
                starts.append(cur.t_ns)
            elif prev.level == 0 and cur.level == 1:
                stops.append(cur.t_ns)

        # Walk through START/STOP pairs and decode bytes between them
        si, ei = 0, 0
        while si < len(starts):
            t_start = starts[si]
            t_stop = stops[ei] if ei < len(stops) and stops[ei] > t_start else None

            frames.append(ProtoFrame(
                t_ns=t_start,
                type="start",
                data={"text": "START"},
            ))

            if t_stop is None:
                break

            # Collect SCL rising edges between START and STOP
            scl_rising = self._scl.rising_edges(t_start, t_stop)
            bit_idx = 0

            # First byte: address + R/W
            addr_bits = []
            for j in range(bit_idx, min(bit_idx + 8, len(scl_rising))):
                sda_val = self._sda.level_at(scl_rising[j])
                addr_bits.append(sda_val)
                frames.append(ProtoFrame(
                    t_ns=scl_rising[j],
                    type="bit",
                    data={"bit": j - bit_idx, "value": sda_val, "context": "address"},
                ))
            bit_idx += 8

            if len(addr_bits) == 8:
                addr = sum(b << (7 - i_byte) for i_byte, b in enumerate(addr_bits))
                rw = addr & 1
                addr7 = addr >> 1

                # ACK for address (9th bit)
                ack = 0
                ack_error = False
                if bit_idx < len(scl_rising):
                    ack = self._sda.level_at(scl_rising[bit_idx])
                    ack_error = (ack != 0)
                    frames.append(ProtoFrame(
                        t_ns=scl_rising[bit_idx],
                        type="ack" if ack == 0 else "nack",
                        data={"ack": ack == 0},
                        errors=["NACK on address"] if ack_error else [],
                    ))
                    bit_idx += 1

                frames.append(ProtoFrame(
                    t_ns=t_start,
                    type="address",
                    data={
                        "address": addr7,
                        "hex": f"0x{addr7:02X}",
                        "rw": "read" if rw else "write",
                    },
                    errors=["NACK on address"] if ack_error else [],
                ))

                # Subsequent data bytes
                while bit_idx + 8 <= len(scl_rising):
                    data_byte_start = bit_idx
                    data_bits_val = []
                    for j in range(data_byte_start, data_byte_start + 8):
                        sda_val = self._sda.level_at(scl_rising[j])
                        data_bits_val.append(sda_val)
                        frames.append(ProtoFrame(
                            t_ns=scl_rising[j],
                            type="bit",
                            data={"bit": j - data_byte_start, "value": sda_val, "context": "data"},
                        ))
                    bit_idx += 8

                    data_byte = sum(b << (7 - i_byte) for i_byte, b in enumerate(data_bits_val))

                    # ACK/NACK
                    data_ack = 0
                    data_ack_err = False
                    if bit_idx < len(scl_rising):
                        data_ack = self._sda.level_at(scl_rising[bit_idx])
                        data_ack_err = (data_ack != 0)
                        frames.append(ProtoFrame(
                            t_ns=scl_rising[bit_idx],
                            type="ack" if data_ack == 0 else "nack",
                            data={"ack": data_ack == 0},
                            errors=["NACK"] if data_ack_err else [],
                        ))
                        bit_idx += 1

                    texts = [chr(data_byte) if 32 <= data_byte < 127 else f"[{data_byte:02X}]"]
                    frames.append(ProtoFrame(
                        t_ns=scl_rising[data_byte_start],
                        type="data",
                        data={
                            "byte": data_byte,
                            "hex": f"0x{data_byte:02X}",
                            "text": texts[0],
                        },
                        errors=["NACK"] if data_ack_err else [],
                    ))

                    if data_ack_err:
                        break

            frames.append(ProtoFrame(
                t_ns=t_stop,
                type="stop",
                data={"text": "STOP"},
            ))

            # Advance to next START after this STOP
            si += 1
            while ei < len(stops) and stops[ei] <= t_stop:
                ei += 1

        return frames


def main():
    ap = argparse.ArgumentParser(description="Native I²C decoder for ATK-Logic captures")
    ap.add_argument("file", help="Path to .atkdl or .bin file")
    ap.add_argument("--scl", type=int, required=True, help="SCL channel ID")
    ap.add_argument("--sda", type=int, required=True, help="SDA channel ID")
    ap.add_argument("--addressing", default="7bit", help="7bit (default) / 10bit")
    ap.add_argument("--start", default="0", help="Start time (ns/us/ms/s)")
    ap.add_argument("--end", default=None, help="End time (ns/us/ms/s)")
    args = ap.parse_args()

    from capture_reader import parse_time
    start_ns = parse_time(args.start)
    end_ns = parse_time(args.end) if args.end else None

    reader = CaptureReader(args.file)
    edges_scl = reader.read_edges(args.scl, start_ns, end_ns)
    edges_sda = reader.read_edges(args.sda, start_ns, end_ns)
    opts = normalize_options(vars(args))
    dec = I2CDecoder(edges_scl, edges_sda, reader.get_info().sample_rate_hz, opts)
    frames = dec.decode()

    print(json.dumps(
        [{"t_ns": f.t_ns, "type": f.type, "data": f.data, "errors": f.errors}
         for f in frames], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
