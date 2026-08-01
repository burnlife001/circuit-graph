# -*- coding: utf-8 -*-
"""
netlist_signalmap.py — 从 DXP/Altium 网表(.NET)提取 MCU 各引脚的真实板上信号名,
并用 __shared_docs 下匹配芯片的引脚功能 JSON 补全网表无法命名的电源/模拟引脚, 形成闭环。

若网表附近存在项目源码(.c/.h), 还会扫描源码中形如 `#define CC1IO GPIOC->ODR_F.P13`
的 GPIO 寄存器位宏, 在输出表中增加“源码标识”列, 把芯片引脚与固件里的命名关联起来。

用法:
  python netlist_signalmap.py [netlist.NET]
        [--designator U2] [--package LQFP48]
        [--shared-docs DIR] [--source-dir DIR] [--out OUT.md]

默认:
  netlist      = 当前目录下第一个 *.NET
  designator   = 自动识别(值字段最匹配某芯片 JSON 的元件)
  package      = 自动识别(由该元件封装/值字段匹配 JSON 的 packages)
  shared-docs  = 从网表目录逐级上溯, 找到的第一个 __shared_docs 目录
  source-dir   = 从网表目录逐级上溯, 第一个包含 .c/.h 子目录的目录
  out          = SCH-<chip_name>.md (例如 SCH-MM32F0140.md)

命名优先级(每个引脚):
  1. 网表显式网络标签(非 NetXxx_n)          -> 直接采用
  2. NetXxx_n 但网络内挂带名测试点(TP 封装)  -> 用测试点值字段
  3. NetXxx_n 且 JSON 判定为电源引脚(type=S) -> 用 JSON 主功能名(VDDA/VSS/...)  ← 闭环补全
  4. NetXxx_n 但固件源码有明确 GPIO 宏名      -> 直接采用源码标识宏名  ← 源码闭环
  5. 引脚未出现在任何网络                      -> 标记为 NC(悬空/未连接), 无需确认
  6. 其余无法判定                              -> 保留 NetXxx_n 并标注必须拓扑推断
"""
import sys, os, re, json, glob, argparse


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


def dir_has_source_files(d):
    """判断目录 d 本身或任意一级子目录下是否存在 .c/.h 文件。"""
    try:
        entries = os.listdir(d)
    except OSError:
        return False
    for entry in entries:
        p = os.path.join(d, entry)
        if os.path.isfile(p) and entry.endswith((".c", ".h")):
            return True
        if os.path.isdir(p):
            try:
                for f in os.listdir(p):
                    if f.endswith((".c", ".h")):
                        return True
            except OSError:
                pass
    return False


def find_source_root(start):
    """从 start 目录逐级上溯, 找到第一个包含源码子目录的目录作为项目根。"""
    d = os.path.abspath(start)
    while True:
        if dir_has_source_files(d):
            return d
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
    """返回 (designator, footprint, value, core)。值字段与某芯片名 LCP 最长者。"""
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


# 匹配形如 #define CC1IO GPIOC->ODR_F.P13
GPIO_DEFINE_RE = re.compile(
    r"^\s*#\s*define\s+(\w+)\s+.*?\b(GPIO[A-Z])->\w+\.P(\d+)\b",
    re.MULTILINE,
)


def scan_source_identifiers(source_root):
    """
    扫描 source_root 下所有 .c/.h, 返回 dict[(port_letter, pin_number)] = [(name, rel_path, line), ...]
    目前主要捕获 `#define NAME GPIOx->REG.Pn` 形式的宏定义。
    过滤掉与引脚名本身完全相同的宏(如 PA9 = GPIOA->...P9),避免无信息冗余。
    """
    ids = {}
    exclude_dirs = {"__shared_docs", ".git", "__pycache__", "node_modules"}
    for root, dirs, files in os.walk(source_root):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for fname in files:
            if not fname.endswith((".c", ".h")):
                continue
            path = os.path.join(root, fname)
            rel = os.path.relpath(path, source_root).replace("\\", "/")
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except Exception:
                continue
            for m in GPIO_DEFINE_RE.finditer(text):
                name = m.group(1)
                port = m.group(2)[-1]   # 'A'..'D'
                pin = int(m.group(3))
                line = text[:m.start()].count("\n") + 1
                # 跳过与引脚名完全一致的宏, 例如 #define PA9 GPIOA->ODR_F.P9
                if name.upper() == f"P{port}{pin}":
                    continue
                ids.setdefault((port, pin), []).append((name, rel, line))
    return ids


def parse_chip_pin_name(name):
    """把 'PC13' / 'PA1' 解析为 ('C', 13) / ('A', 1); 解析失败返回 None。"""
    m = re.match(r"^P([A-D])(\d+)$", name or "")
    if not m:
        return None
    return (m.group(1), int(m.group(2)))


def name_consistent(board, macros):
    """
    判断原理图网络名 board 与固件宏名集合 macros 是否“同一信号”。
    一致条件(任一满足即视为一致, 不再提示):
      - 归一化后互为子串(SDA ↔ SDA_OUT, TX ↔ TXD)
      - 归一化后最长公共前缀 >= 3(T1PWMN ↔ T1PWM1N)
    全部宏都不满足 -> 视为异名, 仅给 ⓘ 表格提示(非 ⚠, 不触发拓扑推断)。
    """
    nb = norm(board)
    for mname in macros:
        nm = norm(mname)
        if not nb or not nm:
            continue
        if nb in nm or nm in nb:
            return True
        if lcp(nb, nm) >= 3:
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("netlist", nargs="?")
    ap.add_argument("--designator")
    ap.add_argument("--package")
    ap.add_argument("--shared-docs")
    ap.add_argument("--source-dir")
    ap.add_argument("--out")
    a = ap.parse_args()

    netlist = a.netlist
    if not netlist:
        cands = glob.glob("*.NET") + glob.glob("*.net")
        if not cands:
            sys.exit("未找到 .NET 网表, 请显式指定")
        netlist = cands[0]
    netlist = os.path.abspath(netlist)
    netlist_dir = os.path.dirname(netlist)

    # 跑之前原封不动先念给用户
    sys.stderr.write(
        "\n"
        "- 鉴于很多人SCH-PCB设计不同步,强制要求从原理图生成网表文件\n"
        "- 除NC脚外,mcu的每一个引脚都放上你的网络标号,否则无法识别\n"
        "- 禁止多个网络标号连接在一起\n"
        "\n"
    )

    shared = a.shared_docs or find_shared_docs(netlist_dir)
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

    # 引脚 -> 所在网络
    pin2net = {}
    for name, nodes in nets.items():
        for d, pin in nodes:
            if d == desig and pin.isdigit():
                pin2net[int(pin)] = name

    # 源码自动检测
    source_root = a.source_dir
    if source_root:
        source_root = os.path.abspath(source_root)
    else:
        source_root = find_source_root(netlist_dir)
    source_ids = scan_source_identifiers(source_root) if source_root else {}
    has_source = bool(source_ids)

    rows = []
    for pin in range(1, total + 1):
        ci = pinmap.get(pin, {})
        chipfn = ci.get("name", "")

        # 先解析源码标识(宏名 + 出现位置), 供后续自动命名兜底使用
        src_names = []   # 该脚在固件里出现的宏名, 去重保序
        src_id = ""
        if has_source:
            parsed = parse_chip_pin_name(chipfn)
            if parsed:
                matches = source_ids.get(parsed)
                if matches:
                    seen = set()
                    parts = []
                    for name, rel, line in matches:
                        key = (name, rel, line)
                        if key in seen:
                            continue
                        seen.add(key)
                        if name not in src_names:
                            src_names.append(name)
                        parts.append(f"{name} ({rel}:{line})")
                    src_id = "<br>".join(parts)

        net = pin2net.get(pin)
        if net is None:
            board, src = "NC", "悬空无连接 → 标记 NC"
        elif not net.startswith("Net"):
            board, src = net, "网表网络标签"
            # 网表已命名, 但固件宏名与之不一致 -> 仅 ⓘ 提示, 不属于 ⚠
            if src_names and not name_consistent(board, src_names):
                src += f" · ⓘ源码作 {src_names[0]}"
        else:
            tp = next((comp[d][1] for d, _ in nets[net]
                       if comp.get(d, ("", ""))[0].startswith("TP")
                       and is_signal_name(comp[d][1])), None)
            if tp:
                board, src = tp, f"测试点值字段({net})"
            elif ci.get("type") == "S":
                board, src = chipfn, f"JSON电源功能({net})"
            elif src_names:
                # 优先级 4.5: 自动命名(NetXxx_n)但固件里有明确宏名 -> 直接采用源码标识
                board, src = src_names[0], f"源码标识({net})"
            else:
                board, src = net, "⚠ 自动命名, 必须拓扑推断"

        rows.append((pin, chipfn, board, src, src_id))

    out = a.out or os.path.join(netlist_dir, f"SCH-{j.get('chip_name', desig)}.md")
    buf = []
    buf.append(f"# {j.get('chip_name')} ({desig}) 板上信号名 — 网表 + JSON 闭环提取\n")
    src_note = ""
    if source_root:
        src_note = f"  |  源码根: `{os.path.relpath(source_root, netlist_dir)}`"
    buf.append(f"> 网表: `{os.path.basename(netlist)}`  |  封装: `{package}`  "
               f"|  芯片JSON: `{os.path.relpath(jp, netlist_dir)}`{src_note}\n")
    buf.append("\n## 原理图\n")
    if has_source:
        buf.append("| 引脚号 | 芯片功能 | 板上信号名 | 来源 | 源码标识 |")
        buf.append("|:------:|----------|-----------|------|----------|")
        for pin, chipfn, board, src, src_id in rows:
            buf.append(f"| {pin} | {chipfn} | {board} | {src} | {src_id} |")
    else:
        buf.append("| 引脚号 | 芯片功能 | 板上信号名 | 来源 |")
        buf.append("|:------:|----------|-----------|------|")
        for pin, chipfn, board, src, _ in rows:
            buf.append(f"| {pin} | {chipfn} | {board} | {src} |")

    text = "\n".join(buf) + "\n"
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    sys.stderr.write(f"[ok] MCU={desig} chip={j.get('chip_name')} package={package}\n"
                     f"[ok] 输出 -> {out}\n")
    if has_source:
        sys.stderr.write(f"[ok] 源码根 -> {source_root}\n")
    print(text)


if __name__ == "__main__":
    main()
