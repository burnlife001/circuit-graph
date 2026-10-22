#!/usr/bin/env python3
"""MM32 Serial Monitor — pyserial-based CLI for CH340/COM port debugging.

Usage:
    python serial_monitor.py                           # auto-detect CH340, connect at 115200
    python serial_monitor.py --port COM7               # specific port
    python serial_monitor.py --baud 9600               # custom baud rate
    python serial_monitor.py --list                    # list all serial ports
    python serial_monitor.py --hex                     # display hex instead of ASCII
    python serial_monitor.py --timeout 5               # exit after N seconds idle
    python serial_monitor.py --log capture.log         # save output to file
    python serial_monitor.py --send k1                 # send command then monitor response
"""

import argparse
import configparser
import enum
import signal
import sys
import time
from pathlib import Path

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("[ERROR] pyserial not installed. Run: pip install pyserial")
    sys.exit(1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def find_ch340_ports():
    """Return list of (device, description) for CH340-based COM ports."""
    ch340 = []
    all_ports = []
    for p in serial.tools.list_ports.comports():
        all_ports.append((p.device, p.description))
        if "CH340" in p.description.upper():
            ch340.append((p.device, p.description))
    return ch340, all_ports


def _is_port_available(port):
    """Probe whether *port* can be opened right now.

    Returns `(True, "")` if the port is free, otherwise `(False, reason)`.
    """
    try:
        with serial.Serial(port, timeout=0.5):
            return True, ""
    except serial.SerialException as e:
        err = str(e)
        if "PermissionError" in err or "Access is denied" in err:
            return False, f"{port} is already open by another process (e.g., ATK-Logic, PuTTY)."
        return False, err
    except Exception as e:  # pragma: no cover
        return False, str(e)


def list_ports():
    """Print all serial ports, marking CH340."""
    ch340, all_ports = find_ch340_ports()
    if not all_ports:
        print("[INFO] No serial ports detected.")
        return

    print(f"{'PORT':<12} DESCRIPTION")
    print("-" * 50)
    for dev, desc in all_ports:
        marker = " ★ CH340" if any(dev == c[0] for c in ch340) else ""
        print(f"{dev:<12} {desc}{marker}")

    if ch340:
        print(f"\n[INFO] Found {len(ch340)} CH340 port(s).")


# ---------------------------------------------------------------------------
# Command definitions — send predefined strings to the MCU
# ---------------------------------------------------------------------------

class CMDS(enum.Enum):
    k1 = "k1"
    k2 = "k2"
    k3 = "k3"
    k4 = "k4"


def sendcmd(ser, cmd):
    """Send a CMDS command string to the MCU over the open serial port."""
    data = (cmd.value + "\r\n").encode("ascii")
    ser.write(data)
    ser.flush()
    print(f"[SEND] {cmd.value}")


# ---------------------------------------------------------------------------
# INI config persistence — remember last-used settings between sessions
# ---------------------------------------------------------------------------

INI_PATH = Path(__file__).parent / "serial_monitor.ini"


def load_ini_config():
    """Load saved configuration from INI file. Returns dict with typed values."""
    config = configparser.ConfigParser()
    if not INI_PATH.exists():
        return {}
    config.read(INI_PATH, encoding="utf-8")
    if not config.has_section("serial"):
        return {}

    raw = dict(config.items("serial"))
    result = {}

    # Integer fields
    for key in ("baud", "bytesize", "stopbits"):
        if key in raw:
            result[key] = int(raw[key])

    # Float field
    if "timeout" in raw and raw["timeout"].strip():
        result["timeout"] = float(raw["timeout"])

    # Boolean field
    if "hex" in raw:
        result["hex"] = raw["hex"].lower() == "true"

    # String fields
    for key in ("port", "parity"):
        if key in raw:
            result[key] = raw[key]

    return result


def save_ini_config(args):
    """Save current configuration to INI file."""
    config = configparser.ConfigParser()
    config["serial"] = {}

    if args.port:
        config["serial"]["port"] = args.port
    config["serial"]["baud"] = str(args.baud)
    config["serial"]["bytesize"] = str(args.bytesize)
    config["serial"]["parity"] = args.parity
    config["serial"]["stopbits"] = str(args.stopbits)
    config["serial"]["hex"] = str(args.hex)
    if args.timeout is not None:
        config["serial"]["timeout"] = str(args.timeout)

    with open(INI_PATH, "w", encoding="utf-8") as f:
        config.write(f)


# ---------------------------------------------------------------------------
# Signal handler — graceful exit on Ctrl+C
# ---------------------------------------------------------------------------

_keep_running = True


def _on_sigint(signum, frame):
    global _keep_running
    _keep_running = False


# ---------------------------------------------------------------------------
# Reader loop
# ---------------------------------------------------------------------------

def read_loop(ser, *, fmt="ascii", log_file=None, timeout=None):
    """Blocking read loop. Returns on Ctrl+C (SIGINT) or idle timeout."""
    global _keep_running
    _keep_running = True
    signal.signal(signal.SIGINT, _on_sigint)

    last_data_ts = time.time()
    line_count = 0

    print(f"[INFO] Connected to {ser.port} @ {ser.baudrate} baud ({ser.bytesize}{ser.parity[0]}{ser.stopbits})")
    print("[INFO] Press Ctrl+C to stop.\n")

    fh = open(log_file, "w", encoding="utf-8") if log_file else None

    try:
        while _keep_running:
            try:
                if ser.in_waiting:
                    raw = ser.readline()
                    if not raw:
                        continue

                    last_data_ts = time.time()
                    line_count += 1

                    if fmt == "hex":
                        text = raw.hex(" ").upper()
                    else:
                        text = raw.decode("ascii", errors="replace").rstrip("\n").rstrip("\r")

                    print(text, flush=True)

                    if fh:
                        fh.write(text + "\n")
                        fh.flush()

                else:
                    time.sleep(0.01)  # 10ms poll

                    # Idle timeout check
                    if timeout and (time.time() - last_data_ts > timeout):
                        print(f"\n[INFO] Idle timeout ({timeout}s) reached. Disconnecting.")
                        break

            except serial.SerialException as e:
                print(f"\n[ERROR] Serial error: {e}")
                break

    finally:
        if fh:
            fh.close()
            print(f"\n[INFO] Log saved to: {Path(log_file).resolve()}")
        ser.close()
        print(f"[INFO] Disconnected. {line_count} lines received.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main():
    ini = load_ini_config()

    parser = argparse.ArgumentParser(
        description="MM32 Serial Monitor — read serial output from MM32/STM32 MCUs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""Examples:
  python serial_monitor.py                         # auto CH340, 115200
  python serial_monitor.py --list                  # show all ports
  python serial_monitor.py --port COM7 --baud 9600 # custom settings
  python serial_monitor.py --hex --log out.hex     # hex dump to file
  python serial_monitor.py --timeout 10            # exit after 10s idle
  python serial_monitor.py --send k1               # send "k1" then monitor response

Configuration is persisted to serial_monitor.ini alongside this script.
Delete or edit that file to reset defaults.
""",
    )
    parser.add_argument("--port", default=ini.get("port"),
                        help="COM port (e.g. COM7). Auto-detect CH340 if omitted.")
    parser.add_argument("--baud", type=int, default=ini.get("baud", 115200),
                        help="Baud rate (default: 115200)")
    parser.add_argument("--bytesize", type=int, default=ini.get("bytesize", 8),
                        choices=[5, 6, 7, 8], help="Byte size (default: 8)")
    parser.add_argument("--parity", default=ini.get("parity", "N"),
                        choices=["N", "E", "O", "M", "S"], help="Parity (default: N)")
    parser.add_argument("--stopbits", type=int, default=ini.get("stopbits", 1),
                        choices=[1, 2], help="Stop bits (default: 1)")
    parser.add_argument("--timeout", type=float, default=ini.get("timeout"),
                        help="Idle timeout in seconds before auto-disconnect")
    parser.add_argument("--hex", action="store_true", default=ini.get("hex", False),
                        help="Display as hex instead of ASCII")
    parser.add_argument("--log", metavar="FILE", default="C:/Users/yg/AppData/Local/Temp/yg8_uart.txt",
                        help="Save output to file")
    parser.add_argument("--send", metavar="CMD", type=lambda s: CMDS[s.lower()],
                        choices=list(CMDS), help=f"Send command to MCU: {[c.value for c in CMDS]}")
    parser.add_argument("--list", action="store_true",
                        help="List available serial ports and exit")

    args = parser.parse_args()

    # --list mode (do not persist config)
    if args.list:
        list_ports()
        return

    # Resolve port
    port = args.port
    if not port:
        ch340, _ = find_ch340_ports()
        if len(ch340) == 0:
            print("[ERROR] No CH340 port found. Use --list to view available ports, then --port to specify.")
            sys.exit(1)

        # Try CH340 candidates in order; skip ones that are currently occupied.
        available = None
        occupied_reasons = []
        for dev, desc in ch340:
            ok, reason = _is_port_available(dev)
            if ok:
                available = dev
                break
            occupied_reasons.append(f"  {dev}: {reason}")

        if available is None:
            print(f"[ERROR] Found {len(ch340)} CH340 port(s), but none can be opened.")
            for r in occupied_reasons:
                print(r)
            print("[HINT] Close other serial tools (ATK-Logic, PuTTY, etc.) and retry.")
            sys.exit(1)

        if len(ch340) > 1:
            print(f"[INFO] Multiple CH340 ports found, using first available: {available}")
        port = available

    # Persist resolved config for next session
    args.port = port
    save_ini_config(args)

    # Build serial config
    parity_map = {"N": serial.PARITY_NONE, "E": serial.PARITY_EVEN, "O": serial.PARITY_ODD,
                  "M": serial.PARITY_MARK, "S": serial.PARITY_SPACE}
    stop_map = {1: serial.STOPBITS_ONE, 2: serial.STOPBITS_TWO}
    byte_map = {5: serial.FIVEBITS, 6: serial.SIXBITS, 7: serial.SEVENBITS, 8: serial.EIGHTBITS}

    try:
        ser = serial.Serial(
            port=port,
            baudrate=args.baud,
            bytesize=byte_map[args.bytesize],
            parity=parity_map[args.parity],
            stopbits=stop_map[args.stopbits],
            timeout=0.5,  # read timeout
        )
    except serial.SerialException as e:
        err = str(e)
        print(f"[ERROR] Cannot open {port}: {e}")
        if "PermissionError" in err or "Access is denied" in err:
            print("[HINT] Port is in use by another application (e.g., ATK-Logic, PuTTY). Close it and retry.")
        else:
            print("[HINT] Use --list to see available ports.")
        sys.exit(1)

    # Send command if --send specified
    if args.send:
        sendcmd(ser, args.send)

    read_loop(ser, fmt="hex" if args.hex else "ascii", log_file=args.log, timeout=args.timeout)


if __name__ == "__main__":
    main()
