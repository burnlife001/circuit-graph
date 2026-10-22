# -*- coding: utf-8 -*-
"""
netlist_signalmap.py — 从 DXP/Altium 网表(.NET)提取 MCU 各引脚的真实板上信号名,
并用 __shared_docs 下匹配芯片的引脚功能 JSON 补全电源/模拟引脚, 形成闭环。

用法:
  python netlist_signalmap.py [netlist.NET]
        [--designator U2] [--package LQFP48]
        [--shared-docs DIR] [--out OUT.md]

默认:
  netlist      = 当前目录下第一个 *.NET
  designator   = 自动识别(值字段最匹配某芯片 JSON 的元件)
  package      = 自动识别(由该元件封装/值字段匹配 JSON 的 packages)
  shared-docs  = 从网表目录逐级上溯, 找到的第一个 __shared_docs 目录
  out          = <designator>_signalmap.md

命名优先级(每个引脚逐条降级, 工具自动完成全部分析):
  1. 网表显式网络标签(非 NetXxx_n)          -> 直接采用
  2. NetXxx_n 但网络内挂带名测试点(TP 封装)  -> 用测试点值字段
  3. NetXxx_n 且 JSON 判定为电源引脚(type=S) -> 用 JSON 主功能名(VDDA/VSS/...)
  4. NetXxx_n 经串联 R/L 追溯到相邻具名网络   -> 用该相邻网络名(标注经哪个器件推断)
  5. 引脚未出现在任何网络                      -> 留空(悬空/NC), 依据列注 JSON 芯片功能
  6. 仍无法判定                                -> 保留 NetXxx_n, 依据列列出该网络全部连接点
"""
import argparse
import glob
import json
import os
import re
import sys

POWER_RAILS = {"GND", "PGND", "CGND", "AGND", "DGND", "VBAT", "VBAK",
               "VDD", "VDDA", "VSS", "VSSA"}


def norm(s):
    return re.sub(r"[^A-Za-z0-9]", "", s or "").upper()


def lcp(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


def find_shared_docs(start):
    d = os.path.abspath(start)
    while True:
        cand = os.path.join(d, "__shared_docs")
        if os.path.isdir(cand):
            return cand
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def parse_netlist(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()
    comp = {}   # designator -> (footprint, value)
    for blk in re.findall(r"\[\s*\n(.*?)\n\]", text, re.S):
        ls = [x.strip() for x in blk.splitlines() if x.strip()]
        if len(ls) >= 3:
            comp[ls[0]] = (ls[1], ls[2])
    nets = {}   # netname -> [(desig, pin)]
    for blk in re.findall(r"\(\s*\n(.*?)\n\)", text, re.S):
        ls = [x.strip() for x in blk.splitlines() if x.strip()]
        if not ls:
            continue
        nodes = []
        for nd in ls[1:]:
            m = re.match(r"^(.+)-([0-9A-Za-z]+)$", nd)
            if m:
                nodes.append((m.group(1), m.group(2)))
        nets[ls[0]] = nodes
    return comp, nets


def load_chip_jsons(shared):
    out = []
    for jp in glob.glob(os.path.join(shared, "**", "*.json"), recursive=True):
        try:
            j = json.load(open(jp, encoding="utf-8"))
        except Exception:
            continue
        if "chip_name" in j and "pins" in j:
            out.append((jp, j))
    return out


def pick_mcu(comp, chips):
    names = [norm(j.get("chip_name", "")) for _, j in chips]
    best = None
    for desig, (fp, val) in comp.items():
        core = norm(val.split("-")[0].split()[0] if val else "")
        score = max((lcp(core, n) for n in names), default=0)
        if score >= 6 and (best is None or score > best[0]):
            best = (score, desig, fp, val, core)
    if not best:
        return None
    return best[1], best[2], best[3], best[4]


def pick_chip(core, chips):
    best = None
    for jp, j in chips:
        s = lcp(core, norm(j.get("chip_name", "")))
        if best is None or s > best[0]:
            best = (s, jp, j)
    return best[1], best[2]


def pick_package(fp, val, j):
    pkgs = j.get("packages", [])
    npkgs = {p: norm(p) for p in pkgs}
    tokens = [norm(fp)] + [norm(t) for t in re.split(r"[-\s]", val or "") if t]
    for tok in tokens:
        if not tok:
            continue
        for p, np_ in npkgs.items():
            if tok == np_:
                return p
        for p, np_ in npkgs.items():
            if tok and (tok in np_ or np_ in tok):
                return p
    return pkgs[0] if pkgs else None


def chip_pinmap(j, package):
    m = {}
    for p in j.get("pins", []):
        n = p.get("packages", {}).get(package)
        if isinstance(n, int):
            m[n] = {"name": p.get("name", ""),
                    "type": p.get("type", ""),
                    "main": p.get("main_function", "")}
    return m


def is_signal_name(v):
    return bool(re.match(r"^[A-Za-z][A-Za-z0-9_+]*$", v or "")) and not re.match(r"^TP\d", v or "")


def cls(desig):
    m = re.match(r"^([A-Za-z]+)", desig)
    return m.group(1).upper() if m else ""


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
        cands = glob.glob("*.NET") + glob.glob("*.net")
        if not cands:
            sys.exit("未找到 .NET 网表, 请显式指定")
        netlist = cands[0]
    netlist = os.path.abspath(netlist)

    shared = a.shared_docs or find_shared_docs(os.path.dirname(netlist))
    if not shared:
        sys.exit("未找到 __shared_docs 目录(逐级上溯失败)")
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
            sys.exit("无法自动识别 MCU 元件, 请用 --designator 指定")
        desig, fp, val, core = picked

    jp, j = pick_chip(core, chips)
    package = a.package or pick_package(fp, val, j)
    pinmap = chip_pinmap(j, package)
    total = max(pinmap) if pinmap else 0

    # 反向索引: 连接点 -> 网络;  元件 -> 引脚集合
    node2net, comp_pins = {}, {}
    for name, nodes in nets.items():
        for d, p in nodes:
            node2net[(d, p)] = name
            comp_pins.setdefault(d, set()).add(p)

    pin2net = {int(p): name for name, nodes in nets.items()
               for d, p in nodes if d == desig and p.isdigit()}

    def trace_series(netname):
        """经串联 R/L(两端器件)追溯到相邻具名网络, 返回 [(相邻网络名, 经由器件), ...]"""
        out = []
        for d, p in nets[netname]:
            if cls(d) in ("R", "L") and len(comp_pins.get(d, ())) == 2:
                other = next(x for x in comp_pins[d] if x != p)
                adj = node2net.get((d, other))
                if adj and not adj.startswith("Net") and norm(adj) not in {norm(x) for x in POWER_RAILS}:
                    out.append((adj, d))
        return out

    rows = []
    for pin in range(1, total + 1):
        ci = pinmap.get(pin, {})
        chipfn = ci.get("name", "")
        net = pin2net.get(pin)
        if net is None:
            board, src = "", ("NC(芯片未引出)" if chipfn == "NC" else f"悬空/未连接(芯片功能 {chipfn})")
        elif not net.startswith("Net"):
            board, src = net, "网表网络标签"
        else:
            tp = next((comp[d][1] for d, _ in nets[net]
                       if comp.get(d, ("", ""))[0].startswith("TP")
                       and is_signal_name(comp[d][1])), None)
            traced = trace_series(net)
            if tp:
                board, src = tp, f"测试点值字段(原 {net})"
            elif ci.get("type") == "S":
                board, src = chipfn, f"JSON电源功能(原 {net})"
            elif traced:
                names = sorted({n for n, _ in traced})
                board = names[0]
                via = ", ".join(f"{n}←{d}" for n, d in traced)
                src = f"经串接器件推断(原 {net}; {via})"
            else:
                conn = " ".join(f"{d}-{p}" for d, p in nets[net] if d != desig)
                board, src = net, f"⚠ 无标签/测试点/电源特征; 该网连接: {conn or '(仅本脚)'}"
        rows.append((pin, chipfn, ci.get("type", ""), net or "", board, src))

    out = a.out or f"{desig}_signalmap.md"
    buf = [f"# {j.get('chip_name')} ({desig}) 板上信号名 — 网表 + JSON 闭环提取\n",
           f"> 网表: `{os.path.basename(netlist)}`  |  封装: `{package}`  "
           f"|  芯片JSON: `{os.path.relpath(jp, os.path.dirname(netlist))}`\n",
           "\n| 引脚号 | 芯片功能 | 类型 | 网表网络 | 板上信号名 | 依据/分析 |",
           "|:------:|----------|:----:|----------|-----------|----------|"]
    for pin, chipfn, typ, net, board, src in rows:
        buf.append(f"| {pin} | {chipfn} | {typ} | {net} | {board} | {src} |")

    n_warn = sum(1 for r in rows if r[5].startswith("⚠"))
    buf.append(f"\n共 {len(rows)} 脚;  仍需人工确认 {n_warn} 脚"
               f"{'(见依据列连接点)' if n_warn else ' —— 全部自动判定完成'}。")

    text = "\n".join(buf) + "\n"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    sys.stderr.write(f"[ok] MCU={desig} chip={j.get('chip_name')} package={package} 待确认={n_warn}\n"
                     f"[ok] 输出 -> {out}\n")
    print(text)


if __name__ == "__main__":
    main()
