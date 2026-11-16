#!/usr/bin/env python3
"""cg 全流程（12345 档）：role -> 引脚/网络 -> 对端 -> KB -> 固件线索。

用法:
    python cg_flow.py R101 [--net-file PATH] [--expand N] [--neighbors N]

默认自动在当前目录下找 *.NET；输出一份可直接回答的紧凑报告。
"""
import argparse
import glob
import json
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

POWER_NETS = {"GND", "CGND", "AGND", "PGND", "VCC", "VDD", "3V3", "3.3V", "5V",
              "5V2", "USB_5V", "VBUS", "DCIN"}


def cg(args):
    """调用 cg CLI，返回 (ok, stdout, stderr)。"""
    env = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    p = subprocess.run(["cg"] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env)
    return p.returncode == 0, p.stdout, p.stderr


def cg_json(args):
    ok, out, err = cg(args + ["--format", "json"])
    if not ok:
        return None, (err or out).strip()
    try:
        return json.loads(out), None
    except json.JSONDecodeError as exc:
        return None, f"JSON 解析失败: {exc}"


def find_net_file(explicit):
    if explicit:
        return explicit
    hits = sorted(glob.glob("**/*.NET", recursive=True))
    if not hits:
        sys.exit("找不到 .NET 文件，请用 --net-file 指定")
    return hits[0]


def step1_role(x, net_file):
    data, err = cg_json(["role", x, net_file])
    if data is None:
        print(f"[1] role 失败: {err}")
        return None
    print(f"[1] role: {data.get('title', '?')}  (rule={data.get('rule')}, confidence={data.get('confidence')})")
    if data.get("summary"):
        print(f"    {data['summary']}")
    for ev in data.get("evidence", []):
        print(f"    evidence: {ev}")
    return data


def step2_pins(x, net_file):
    """返回 [(pin, name, net, [members])]；conn 失败时退回 comp + net。"""
    data, err = cg_json(["query", net_file, "--conn", x])
    if data is None:
        print(f"[2] --conn 失败({err})，改用 --comp")
        comp, err = cg_json(["query", net_file, "--comp", x])
        if comp is None:
            print(f"[2] --comp 也失败: {err}")
            return []
        rows = []
        for sec in comp.get("sections", []):
            for pin in sec.get("pins", []):
                net = sec.get("nets", {}).get(pin["pin"])
                rows.append((pin["pin"], pin.get("name"), net, []))
        return rows

    rows = []
    for sec in data.get("sections", []):
        for pin in sec.get("pins", []):
            members = [m["id"] for m in pin.get("members", []) if m["id"] != x]
            rows.append((pin["pin"], pin.get("name"), pin.get("net"), members))
    return rows


def step3_far_side(x, rows, net_file, expand_nets, neighbor_limit):
    """批量查同网元件另一端的网络，并展开少量关键网。"""
    neighbors = []
    for _, _, net, members in rows:
        if net in POWER_NETS:  # 电源/地网上的去耦电容与本元件功能无关
            continue
        for n in members:
            if n not in neighbors:
                neighbors.append(n)
    neighbors = [n for n in neighbors if not n.startswith("Net")]  # Net* 是自动网名，不是元件
    if not neighbors:
        print("[3] 无同网元件")
        return []
    neighbors = neighbors[:neighbor_limit]
    args = ["query", net_file]
    for n in neighbors:
        args += ["--comp", n]
    data, err = cg_json(args)
    if data is None:
        print(f"[3] 对端查询失败: {err}")
        return neighbors

    own_nets = {net for _, _, net, _ in rows if net}
    print(f"[3] 对端 ({len(neighbors)}): " + ", ".join(neighbors))
    seen_nets = []
    for sec in data.get("sections", []):
        nets = sec.get("nets", {})
        # IC 引脚多时只显示与本元件相关的网，避免刷屏
        if len(nets) > 12:
            keep = [p for p, n in nets.items() if n in own_nets]
            desc = ", ".join(f"{p}->{nets[p]}" for p in keep) or "无公共网"
            desc += f"  (共 {len(nets)} 个引脚)"
        else:
            desc = ", ".join(f"{p}->{nets[p]}" for p in nets)
        print(f"    {sec.get('id')}[{sec.get('value')}]: {desc}")
        # 多引脚 IC 的外围网与本元件无关，不参与展开
        if len(nets) > 12:
            continue
        for net in nets.values():
            if net and net not in POWER_NETS and net not in own_nets \
                    and net not in seen_nets:
                seen_nets.append(net)

    for net in seen_nets[:expand_nets]:
        d, _ = cg_json(["query", net_file, "--net", net])
        if not d:
            continue
        mem = []
        for sec in d.get("sections", []):
            for m in sec.get("members", []) or sec.get("components", []):
                mem.append(f"{m.get('id')}({m.get('value')})")
        if mem:
            print(f"    网 {net}: " + ", ".join(mem))
    return neighbors


def step4_kb(x, neighbors, net_file):
    parts = []
    if x.startswith("U"):
        parts.append(x)
    parts += [n for n in neighbors if n.startswith("U")]
    done = set()
    for ref in parts[:3]:
        d, _ = cg_json(["query", net_file, "--comp", ref])
        if not d:
            continue
        for sec in d.get("sections", []):
            part = sec.get("value")
            if not part or part in done:
                continue
            done.add(part)
            _, out, _ = cg(["kb", "lookup", part])
            try:
                kb = json.loads(out)
            except json.JSONDecodeError:
                print(f"[4] kb {ref}={part}: 无数据")
                continue
            pinout = ", ".join(f"{p}={n}" for p, n in (kb.get("pinout") or {}).items())
            print(f"[4] kb {ref}={part} ({kb.get('package')}): {pinout or '无引脚数据'}")
            if kb.get("description"):
                print(f"    {kb['description'][:100]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("designator")
    ap.add_argument("--net-file", default=None)
    ap.add_argument("--expand", type=int, default=3, help="展开几个关键网")
    ap.add_argument("--neighbors", type=int, default=8, help="查询多少个同网元件")
    args = ap.parse_args()

    x = args.designator.upper()
    net_file = find_net_file(args.net_file)
    print(f"# {x}  @ {net_file}")

    step1_role(x, net_file)
    rows = step2_pins(x, net_file)
    for pin, name, net, members in rows:
        label = f"{name}" if name else ""
        if net in POWER_NETS:
            extra = f" 同网: 电源/地网，{len(members)} 个元件略"
        else:
            shown = members[:8]
            extra = f" 同网: {', '.join(shown)}" if shown else ""
            if len(members) > len(shown):
                extra += f" …(+{len(members) - len(shown)})"
        print(f"[2] pin {pin} {label} -> {net}{extra}")

    neighbors = step3_far_side(x, rows, net_file, args.expand, args.neighbors)
    step4_kb(x, neighbors, net_file)

    nets = sorted({net for _, _, net, _ in rows if net and net not in POWER_NETS})
    pat = "|".join(nets) if nets else x
    print(f"[5] 固件闭环（agent 执行）: cbm search_code(pattern='{pat}', regex=true)")


if __name__ == "__main__":
    main()
