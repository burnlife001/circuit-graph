"""Inspect BD component blocks and pin references in a Protel netlist."""

import sys
from pathlib import Path

net_file = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("fixtures/protel/500W-TX-Main-Zero.NET")
prefix = sys.argv[2] if len(sys.argv) > 2 else "BD"

content = None
for enc in ("utf-8", "gbk", "gb2312", "latin-1"):
    try:
        content = net_file.read_text(encoding=enc)
        break
    except UnicodeDecodeError:
        continue

lines = content.splitlines()

print("=== component blocks ===")
in_block = False
block = []
for line in lines:
    s = line.strip()
    if s == "[":
        in_block = True
        block = []
    elif s == "]":
        in_block = False
        if block and block[0].startswith(prefix):
            print(block)
    elif in_block:
        block.append(s)

print("\n=== pin refs in nets ===")
for line in lines:
    s = line.strip()
    if s.startswith(prefix) and "-" in s:
        print(repr(s))

print("\n=== nets containing target pins ===")
in_net = False
net_name = None
pins = []
for line in lines:
    s = line.strip()
    if s == "(":
        in_net = True
        net_name = None
        pins = []
    elif s == ")":
        in_net = False
        if any(p.startswith(prefix + "-") for p in pins):
            print(f"net={net_name!r} pins={pins}")
    elif in_net:
        if net_name is None and not s.startswith(prefix):
            net_name = s
        elif "-" in s:
            pins.append(s)
