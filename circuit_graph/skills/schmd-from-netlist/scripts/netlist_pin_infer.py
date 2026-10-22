# -*- coding: utf-8 -*-
"""
netlist_pin_infer.py — 为 netlist-signalmap 中"无法确定信号名"的 MCU 引脚导出邻接拓扑,
供 LLM 逐脚推断"疑似功能 + 依据 + 置信度"。配套主脚本 netlist_signalmap.py 使用。

判定为"待定"的脚(主脚本里会落到 NetXxx_n / ⚠ 必须拓扑推断 的):
  A. 悬空(网表里无任何连接)        -> 主脚本已直接标记为 NC, 本脚本跳过
  B. 连了电路但只有 NetXxx_n 自动名  -> 以该脚网络为根做 2 跳 BFS 展开拓扑指纹:
        L0 本网络成员 -> L1 邻件另一端去向 -> L2 再下一级元件去向。
        2 跳才能看穿"串电阻→MOSFET漏极→CC线"这类两级结构;1 跳只能看到一颗电阻。
        在电源/地网络、>8 节点的大网络处自动截断,避免展开爆炸。
已被网络标签/测试点/电源JSON 命名好的脚直接跳过(它们不需要推断)。

用法:
  python netlist_pin_infer.py [netlist.NET] [--designator U2] [--package LQFP48]
                              [--shared-docs DIR] [--out U2_pin_infer.md]
输出既写文件也打到 stdout,便于直接喂给 LLM 解读。
"""
import argparse
import os
import re as _re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from netlist_signalmap import (
    chip_pinmap,
    find_shared_docs,
    is_signal_name,
    load_chip_jsons,
    norm,
    parse_netlist,
    pick_chip,
    pick_mcu,
    pick_package,
)


def build_comp_pins(nets):
    """desig -> [(pin, netname)] ,用于展开邻接元件的其它管脚去向。"""
    cp = {}
    for name, nodes in nets.items():
        for d, pin in nodes:
            cp.setdefault(d, []).append((pin, name))
    return cp


_POWER = _re.compile(
    r"^(GND|PGND|AGND|DGND|[+-]?\d+V\d*|V(DD|SS|CC|BAT|BUS|IN|OUT|REF)\w*)$", _re.I
)


def is_power(net):
    return bool(_POWER.match(net or ""))


def expand_topology(mcu, pin, net0, nets, comp, comp_pins, maxhop=2, busmax=8):
    """以 net0 为根做 maxhop 跳 BFS,打印拓扑指纹;电源/地、大网络处截断。"""
    lines = [f"  L0 本网络 {net0}  成员: "
             + ", ".join(f"{d}-{p}" for d, p in nets.get(net0, []))]
    visited = {net0}
    seen_edge = set()           # (desig,pin) 去重,避免重复打印同一物理节点
    frontier = [net0]
    for hop in range(1, maxhop + 1):
        nxt = []
        for src in frontier:
            for d, _p in nets.get(src, []):
                if d == mcu:
                    continue
                val = comp.get(d, ("?", "?"))[1]
                for pp, nn in comp_pins.get(d, []):
                    if nn == src or (d, pp) in seen_edge:
                        continue
                    seen_edge.add((d, pp))
                    stop = ""
                    if is_power(nn):
                        stop = "  (电源/地, 截断)"
                    elif len(nets.get(nn, [])) > busmax:
                        stop = f"  (大网络 {len(nets[nn])} 节点, 截断)"
                    lines.append(f"  {'  ' * hop}L{hop} {d}-{pp} [{val}] {src} → {nn}{stop}")
                    if nn not in visited and not stop:
                        visited.add(nn)
                        nxt.append(nn)
        frontier = nxt
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("netlist", nargs="?")
    ap.add_argument("--designator")
    ap.add_argument("--package")
    ap.add_argument("--shared-docs")
    ap.add_argument("--out")
    a = ap.parse_args()

    netlist = a.netlist
    if not netlist:
        import glob
        cands = glob.glob("*.NET") + glob.glob("*.net")
        if not cands:
            sys.exit("未找到 .NET 网表, 请显式指定")
        netlist = cands[0]
    netlist = os.path.abspath(netlist)

    shared = a.shared_docs or find_shared_docs(os.path.dirname(netlist))
    if not shared:
        sys.exit("未找到 __shared_docs 目录")
    chips = load_chip_jsons(shared)
    if not chips:
        sys.exit(f"{shared} 下未发现芯片引脚 JSON")

    comp, nets = parse_netlist(netlist)

    if a.designator:
        desig = a.designator
        fp, val = comp.get(desig, ("", ""))
        core = norm(val.split("-")[0].split()[0] if val else desig)
    else:
        picked = pick_mcu(comp, chips)
        if not picked:
            sys.exit("无法自动识别 MCU, 请用 --designator 指定")
        desig, fp, val, core = picked

    jp, j = pick_chip(core, chips)
    package = a.package or pick_package(fp, val, j)
    pinmap = chip_pinmap(j, package)          # pin -> {name,type,main}
    comp_pins = build_comp_pins(nets)
    total = max(pinmap) if pinmap else 0

    pin2net = {}
    for name, nodes in nets.items():
        for d, pin in nodes:
            if d == desig and pin.isdigit():
                pin2net[int(pin)] = name

    cards = []   # (pin, chipname, kind, lines[])
    for pin in range(1, total + 1):
        ci = pinmap.get(pin, {})
        chipfn = ci.get("name", "")
        net = pin2net.get(pin)

        if net is None:
            continue                       # 悬空/未连接脚已在主脚本标记为 NC, 无需推断

        if not net.startswith("Net"):
            continue                           # 已有网络标签, 跳过

        # NetXxx_n: 先排除已被测试点/电源JSON 解析掉的
        tp = next((comp[d][1] for d, _ in nets[net]
                   if comp.get(d, ("", ""))[0].startswith("TP")
                   and is_signal_name(comp[d][1])), None)
        if tp or ci.get("type") == "S":
            continue                           # 测试点/电源已能命名, 不需推断

        # 真·待定: 连了电路但只有自动名 -> 2 跳 BFS 导出拓扑指纹
        lines = expand_topology(desig, pin, net, nets, comp, comp_pins)
        cards.append((pin, chipfn, "待定(自动名)", lines))

    buf = []
    buf.append(f"# {j.get('chip_name')} ({desig}) 待定引脚邻接拓扑 — 供功能推断\n")
    buf.append(f"> 网表: `{os.path.basename(netlist)}` | 封装: `{package}`\n")
    if not cards:
        buf.append("\n**无待定引脚**:所有有连接的脚都已被网络标签/测试点/电源JSON 命名,"
                   "悬空脚仅 NC。无需推断。\n")
    for pin, chipfn, kind, lines in cards:
        buf.append(f"\n## pin{pin} ({chipfn}) — {kind}")
        buf.extend(lines)

    text = "\n".join(buf) + "\n"
    out = a.out or os.path.join(os.path.dirname(netlist), f"{desig}_pin_infer.md")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    sys.stderr.write(f"[ok] MCU={desig} chip={j.get('chip_name')} package={package} "
                     f"待定脚={len(cards)}\n[ok] 输出 -> {out}\n")
    print(text)


if __name__ == "__main__":
    main()
