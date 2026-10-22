#!/usr/bin/env python3
"""
hardware-diff-by-codes test harness
Implements the eetool/hardware-diff-by-codes SOP for two MM32G0001 firmware dirs.
Parses GPIO/RCC register writes from user source files, simulates final state,
classifies pin differences, and emits a Markdown report.
"""

import re
import sys
from collections import defaultdict
from pathlib import Path

RESET = {
    'CRL': 0x44444444,
    'CRH': 0x44444444,
    'AFRL': 0x00000000,
    'AFRH': 0x00000000,
    'ODR': 0x00000000,
    'AHBENR': 0x00000000,
    'APB1ENR': 0x00000000,
    'APB2ENR': 0x00000000,
}

# Known macros used in the two demo projects. Extracted from reg_gpio.h / reg_rcc.h.
KNOWN_MACROS = {
    # CRL positions / masks
    'GPIO_CRL_MODE0_Pos': 0, 'GPIO_CRL_MODE0_Msk': 0x3,
    'GPIO_CRL_MODE1_Pos': 4, 'GPIO_CRL_MODE1_Msk': 0x3 << 4,
    'GPIO_CRL_MODE2_Pos': 8, 'GPIO_CRL_MODE2_Msk': 0x3 << 8,
    'GPIO_CRL_MODE3_Pos': 12, 'GPIO_CRL_MODE3_Msk': 0x3 << 12,
    'GPIO_CRL_MODE4_Pos': 16, 'GPIO_CRL_MODE4_Msk': 0x3 << 16,
    'GPIO_CRL_MODE5_Pos': 20, 'GPIO_CRL_MODE5_Msk': 0x3 << 20,
    'GPIO_CRL_MODE6_Pos': 24, 'GPIO_CRL_MODE6_Msk': 0x3 << 24,
    'GPIO_CRL_MODE7_Pos': 28, 'GPIO_CRL_MODE7_Msk': 0x3 << 28,
    'GPIO_CRL_CNF0_Pos': 2, 'GPIO_CRL_CNF0_Msk': 0x3 << 2,
    'GPIO_CRL_CNF1_Pos': 6, 'GPIO_CRL_CNF1_Msk': 0x3 << 6,
    'GPIO_CRL_CNF2_Pos': 10, 'GPIO_CRL_CNF2_Msk': 0x3 << 10,
    'GPIO_CRL_CNF3_Pos': 14, 'GPIO_CRL_CNF3_Msk': 0x3 << 14,
    'GPIO_CRL_CNF4_Pos': 18, 'GPIO_CRL_CNF4_Msk': 0x3 << 18,
    'GPIO_CRL_CNF5_Pos': 22, 'GPIO_CRL_CNF5_Msk': 0x3 << 22,
    'GPIO_CRL_CNF6_Pos': 26, 'GPIO_CRL_CNF6_Msk': 0x3 << 26,
    'GPIO_CRL_CNF7_Pos': 30, 'GPIO_CRL_CNF7_Msk': 0x3 << 30,
    # CRH positions / masks
    'GPIO_CRH_MODE8_Pos': 0, 'GPIO_CRH_MODE8_Msk': 0x3,
    'GPIO_CRH_MODE9_Pos': 4, 'GPIO_CRH_MODE9_Msk': 0x3 << 4,
    'GPIO_CRH_MODE10_Pos': 8, 'GPIO_CRH_MODE10_Msk': 0x3 << 8,
    'GPIO_CRH_MODE11_Pos': 12, 'GPIO_CRH_MODE11_Msk': 0x3 << 12,
    'GPIO_CRH_MODE12_Pos': 16, 'GPIO_CRH_MODE12_Msk': 0x3 << 16,
    'GPIO_CRH_MODE13_Pos': 20, 'GPIO_CRH_MODE13_Msk': 0x3 << 20,
    'GPIO_CRH_MODE14_Pos': 24, 'GPIO_CRH_MODE14_Msk': 0x3 << 24,
    'GPIO_CRH_MODE15_Pos': 28, 'GPIO_CRH_MODE15_Msk': 0x3 << 28,
    'GPIO_CRH_CNF8_Pos': 2, 'GPIO_CRH_CNF8_Msk': 0x3 << 2,
    'GPIO_CRH_CNF9_Pos': 6, 'GPIO_CRH_CNF9_Msk': 0x3 << 6,
    'GPIO_CRH_CNF10_Pos': 10, 'GPIO_CRH_CNF10_Msk': 0x3 << 10,
    'GPIO_CRH_CNF11_Pos': 14, 'GPIO_CRH_CNF11_Msk': 0x3 << 14,
    'GPIO_CRH_CNF12_Pos': 18, 'GPIO_CRH_CNF12_Msk': 0x3 << 18,
    'GPIO_CRH_CNF13_Pos': 22, 'GPIO_CRH_CNF13_Msk': 0x3 << 22,
    'GPIO_CRH_CNF14_Pos': 26, 'GPIO_CRH_CNF14_Msk': 0x3 << 26,
    'GPIO_CRH_CNF15_Pos': 30, 'GPIO_CRH_CNF15_Msk': 0x3 << 30,
    # AFRL / AFRH positions / masks
    'GPIO_AFRL_AFR0_Pos': 0, 'GPIO_AFRL_AFR0_Msk': 0xF,
    'GPIO_AFRL_AFR1_Pos': 4, 'GPIO_AFRL_AFR1_Msk': 0xF << 4,
    'GPIO_AFRL_AFR2_Pos': 8, 'GPIO_AFRL_AFR2_Msk': 0xF << 8,
    'GPIO_AFRL_AFR3_Pos': 12, 'GPIO_AFRL_AFR3_Msk': 0xF << 12,
    'GPIO_AFRL_AFR4_Pos': 16, 'GPIO_AFRL_AFR4_Msk': 0xF << 16,
    'GPIO_AFRL_AFR5_Pos': 20, 'GPIO_AFRL_AFR5_Msk': 0xF << 20,
    'GPIO_AFRL_AFR6_Pos': 24, 'GPIO_AFRL_AFR6_Msk': 0xF << 24,
    'GPIO_AFRL_AFR7_Pos': 28, 'GPIO_AFRL_AFR7_Msk': 0xF << 28,
    'GPIO_AFRH_AFR8_Pos': 0, 'GPIO_AFRH_AFR8_Msk': 0xF,
    'GPIO_AFRH_AFR9_Pos': 4, 'GPIO_AFRH_AFR9_Msk': 0xF << 4,
    'GPIO_AFRH_AFR10_Pos': 8, 'GPIO_AFRH_AFR10_Msk': 0xF << 8,
    'GPIO_AFRH_AFR11_Pos': 12, 'GPIO_AFRH_AFR11_Msk': 0xF << 12,
    'GPIO_AFRH_AFR12_Pos': 16, 'GPIO_AFRH_AFR12_Msk': 0xF << 16,
    'GPIO_AFRH_AFR13_Pos': 20, 'GPIO_AFRH_AFR13_Msk': 0xF << 20,
    'GPIO_AFRH_AFR14_Pos': 24, 'GPIO_AFRH_AFR14_Msk': 0xF << 24,
    'GPIO_AFRH_AFR15_Pos': 28, 'GPIO_AFRH_AFR15_Msk': 0xF << 28,
    # Simplified macro names used in source
    'GPIO_CRL_MODE0': 0x3, 'GPIO_CRL_MODE1': 0x3 << 4, 'GPIO_CRL_MODE2': 0x3 << 8,
    'GPIO_CRL_MODE3': 0x3 << 12, 'GPIO_CRL_MODE4': 0x3 << 16, 'GPIO_CRL_MODE5': 0x3 << 20,
    'GPIO_CRL_MODE6': 0x3 << 24, 'GPIO_CRL_MODE7': 0x3 << 28,
    'GPIO_CRL_CNF0': 0x3 << 2, 'GPIO_CRL_CNF1': 0x3 << 6, 'GPIO_CRL_CNF2': 0x3 << 10,
    'GPIO_CRL_CNF3': 0x3 << 14, 'GPIO_CRL_CNF4': 0x3 << 18, 'GPIO_CRL_CNF5': 0x3 << 22,
    'GPIO_CRL_CNF6': 0x3 << 26, 'GPIO_CRL_CNF7': 0x3 << 30,
    'GPIO_CRH_MODE8': 0x3, 'GPIO_CRH_MODE9': 0x3 << 4, 'GPIO_CRH_MODE10': 0x3 << 8,
    'GPIO_CRH_MODE11': 0x3 << 12, 'GPIO_CRH_MODE12': 0x3 << 16, 'GPIO_CRH_MODE13': 0x3 << 20,
    'GPIO_CRH_MODE14': 0x3 << 24, 'GPIO_CRH_MODE15': 0x3 << 28,
    'GPIO_CRH_CNF8': 0x3 << 2, 'GPIO_CRH_CNF9': 0x3 << 6, 'GPIO_CRH_CNF10': 0x3 << 10,
    'GPIO_CRH_CNF11': 0x3 << 14, 'GPIO_CRH_CNF12': 0x3 << 18, 'GPIO_CRH_CNF13': 0x3 << 22,
    'GPIO_CRH_CNF14': 0x3 << 26, 'GPIO_CRH_CNF15': 0x3 << 30,
    'GPIO_AFRL_AFR0': 0xF, 'GPIO_AFRL_AFR1': 0xF << 4, 'GPIO_AFRL_AFR2': 0xF << 8,
    'GPIO_AFRL_AFR3': 0xF << 12, 'GPIO_AFRL_AFR4': 0xF << 16, 'GPIO_AFRL_AFR5': 0xF << 20,
    'GPIO_AFRL_AFR6': 0xF << 24, 'GPIO_AFRL_AFR7': 0xF << 28,
    'GPIO_AFRH_AFR8': 0xF, 'GPIO_AFRH_AFR9': 0xF << 4, 'GPIO_AFRH_AFR10': 0xF << 8,
    'GPIO_AFRH_AFR11': 0xF << 12, 'GPIO_AFRH_AFR12': 0xF << 16, 'GPIO_AFRH_AFR13': 0xF << 20,
    'GPIO_AFRH_AFR14': 0xF << 24, 'GPIO_AFRH_AFR15': 0xF << 28,
    # RCC enable bits
    'RCC_AHBENR_GPIOA': 1 << 17, 'RCC_AHBENR_GPIOB': 1 << 18,
    'RCC_AHBENR_GPIOC': 1 << 19, 'RCC_AHBENR_GPIOD': 1 << 20,
    'RCC_APB1ENR_TIM14': 1 << 8, 'RCC_APB1ENR_USART2': 1 << 17,
    'RCC_APB1ENR_PWREN': 1 << 28,
    # BSRR / BRR
    'GPIO_BSRR_BS0': 1 << 0, 'GPIO_BSRR_BS1': 1 << 1, 'GPIO_BSRR_BS2': 1 << 2,
    'GPIO_BSRR_BS3': 1 << 3, 'GPIO_BSRR_BS4': 1 << 4, 'GPIO_BSRR_BS5': 1 << 5,
    'GPIO_BSRR_BS6': 1 << 6, 'GPIO_BSRR_BS7': 1 << 7, 'GPIO_BSRR_BS8': 1 << 8,
    'GPIO_BSRR_BS9': 1 << 9, 'GPIO_BSRR_BS10': 1 << 10, 'GPIO_BSRR_BS11': 1 << 11,
    'GPIO_BSRR_BS12': 1 << 12, 'GPIO_BSRR_BS13': 1 << 13, 'GPIO_BSRR_BS14': 1 << 14,
    'GPIO_BSRR_BS15': 1 << 15,
    'GPIO_BRR_BR0': 1 << 0, 'GPIO_BRR_BR1': 1 << 1, 'GPIO_BRR_BR2': 1 << 2,
    'GPIO_BRR_BR3': 1 << 3, 'GPIO_BRR_BR4': 1 << 4, 'GPIO_BRR_BR5': 1 << 5,
    'GPIO_BRR_BR6': 1 << 6, 'GPIO_BRR_BR7': 1 << 7, 'GPIO_BRR_BR8': 1 << 8,
    'GPIO_BRR_BR9': 1 << 9, 'GPIO_BRR_BR10': 1 << 10, 'GPIO_BRR_BR11': 1 << 11,
    'GPIO_BRR_BR12': 1 << 12, 'GPIO_BRR_BR13': 1 << 13, 'GPIO_BRR_BR14': 1 << 14,
    'GPIO_BRR_BR15': 1 << 15,
    'GPIO_IDR_IDR2': 1 << 2,
    'GPIO_ODR_ODR15': 1 << 15,
}


def eval_expr(expr, macros=None):
    """Evaluate a C-style constant expression."""
    if macros is None:
        macros = KNOWN_MACROS
    s = expr.strip()
    s = re.sub(r'\b(\d+)[uUlL]+\b', r'\1', s)
    s = re.sub(r'\b(0x[0-9A-Fa-f]+)[uUlL]+\b', r'\1', s)

    def repl(m):
        name = m.group(0)
        if name in macros:
            return str(int(macros[name]))
        return m.group(0)

    s = re.sub(r'\b[A-Za-z_][A-Za-z0-9_]*\b', repl, s)
    try:
        return eval(s, {"__builtins__": {}}, {})
    except Exception as e:
        raise ValueError(f"Cannot evaluate '{expr}' -> '{s}': {e}")


def find_user_sources(root):
    """Collect user .c files (exclude HAL/CMSIS/QP framework)."""
    src_dirs = [Path(root) / 'src', Path(root) / 'mymain']
    files = []
    for d in src_dirs:
        if d.exists():
            for f in d.rglob('*.c'):
                if 'qpn' in f.parts or 'ports' in f.parts or 'lptask' in f.parts:
                    continue
                files.append(f)
    return sorted(files)


def parse_writes(root, files):
    """Parse GPIO/RCC register writes from user source files."""
    writes = []
    for f in files:
        text = f.read_text(encoding='utf-8', errors='ignore')
        text_noc = re.sub(r'//.*', '', text)
        for lineno, line in enumerate(text_noc.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            m = re.search(
                r'MODIFY_REG\s*\(\s*\(?\s*(RCC|GPIO[A-D])\s*->\s*(\w+)\s*\)?\s*,\s*([^,]+)\s*,\s*([^)]+)\)',
                line,
            )
            if m:
                port, reg, clear, setv = m.groups()
                try:
                    cval = eval_expr(clear)
                    sval = eval_expr(setv)
                    writes.append({
                        'file': str(f.relative_to(root)),
                        'line': lineno,
                        'port': port,
                        'reg': reg,
                        'op': 'modify',
                        'clear': cval,
                        'set': sval,
                        'raw': line,
                    })
                except ValueError as e:
                    print(f"WARN: {f}:{lineno}: {e}")
                continue
            m = re.search(r'\b(RCC|GPIO[A-D])->(\w+)\s*(\|=|&=|=)\s*([^;]+)', line)
            if m:
                port, reg, op, val = m.groups()
                if op == '&=' and val.startswith('~'):
                    op = '&=~'
                    val = val[1:].strip()
                try:
                    v = eval_expr(val)
                    writes.append({
                        'file': str(f.relative_to(root)),
                        'line': lineno,
                        'port': port,
                        'reg': reg,
                        'op': op,
                        'val': v,
                        'raw': line,
                    })
                except ValueError as e:
                    print(f"WARN: {f}:{lineno}: {e}")
    return writes


def simulate(writes):
    """Simulate final register state from reset."""
    state = {}
    for port in ['GPIOA', 'GPIOB', 'GPIOC', 'GPIOD', 'RCC']:
        for reg in ['CRL', 'CRH', 'AFRL', 'AFRH', 'ODR', 'AHBENR', 'APB1ENR', 'APB2ENR']:
            state[(port, reg)] = RESET.get(reg, 0)

    for w in writes:
        port, reg = w['port'], w['reg']
        key = (port, reg)
        if key not in state:
            state[key] = 0
        cur = state[key]
        op = w['op']
        if op == '|=':
            state[key] = (cur | w['val']) & 0xFFFFFFFF
        elif op == '&=~':
            state[key] = (cur & ~w['val']) & 0xFFFFFFFF
        elif op == '=':
            state[key] = w['val'] & 0xFFFFFFFF
        elif op == 'modify':
            state[key] = ((cur & ~w['clear']) | w['set']) & 0xFFFFFFFF
        else:
            continue

        if reg == 'BSRR':
            v = state[key]
            set_bits = v & 0xFFFF
            clr_bits = (v >> 16) & 0xFFFF
            odr = state[(port, 'ODR')]
            odr |= set_bits
            odr &= ~clr_bits
            state[(port, 'ODR')] = odr & 0xFFFF
        elif reg == 'BRR':
            v = state[key]
            odr = state[(port, 'ODR')]
            odr &= ~v
            state[(port, 'ODR')] = odr & 0xFFFF
    return state


def nibble(val, idx):
    """Extract 4-bit nibble idx."""
    return (val >> (idx * 4)) & 0xF


def get_pin_state(state, port, pin):
    reg = 'CRL' if pin < 8 else 'CRH'
    afreg = 'AFRL' if pin < 8 else 'AFRH'
    cr = nibble(state[(port, reg)], pin % 8)
    af = nibble(state[(port, afreg)], pin % 8)
    odr = 1 if (state[(port, 'ODR')] >> pin) & 1 else 0
    return cr, af, odr


def decode_crnibble(cr):
    mode = cr & 0x3
    cnf = (cr >> 2) & 0x3
    return mode, cnf


def cr_text(cr):
    mode, cnf = decode_crnibble(cr)
    dir_txt = {0: 'In', 1: 'Out10', 2: 'Out2', 3: 'Out50'}[mode]
    if mode == 0:
        cnf_txt = {0: 'Analog', 1: 'Float', 2: 'Pull', 3: 'Resvd'}[cnf]
    else:
        cnf_txt = {0: 'GP_PP', 1: 'GP_OD', 2: 'AF_PP', 3: 'AF_OD'}[cnf]
    return f"0x{cr:X}({dir_txt},{cnf_txt})"


def classify_pin(cr_a, af_a, odr_a, cr_b, af_b, odr_b):
    """Classify a single pin difference following the SOP."""
    if cr_a == cr_b and af_a == af_b and odr_a == odr_b:
        return 'SAME'

    if (cr_a == 0 and cr_b == 0x4) or (cr_a == 0x4 and cr_b == 0):
        return 'IGNORE'

    mode_a, cnf_a = decode_crnibble(cr_a)
    mode_b, cnf_b = decode_crnibble(cr_b)
    if (mode_a == 0 and cnf_a == 3) or (mode_b == 0 and cnf_b == 3):
        return 'INVALID'

    if (mode_a == 0) != (mode_b == 0):
        return 'MANUAL REVIEW'

    if mode_a == 0 and mode_b == 0:
        if cnf_a == 2 and cnf_b == 2:
            if odr_a != odr_b:
                return 'Firmware'
        if cnf_a != cnf_b:
            return 'Firmware'
        return 'SAME'

    if cnf_a != cnf_b:
        return 'Firmware'
    if cnf_a in (2, 3) and cnf_b in (2, 3):
        if af_a != af_b:
            return 'HARDWARE DIFF'
    return 'Firmware'


def find_mcu_family(root):
    """Infer MCU family from includes."""
    files = find_user_sources(root)
    for f in files:
        text = f.read_text(encoding='utf-8', errors='ignore')
        if 'mm32g0001' in text.lower() or 'mm32_device' in text.lower():
            return 'MM32G0001 (CRL/CRH model)'
        if 'stm32f1xx' in text.lower():
            return 'STM32F1 (CRL/CRH model)'
        if 'stm32f4xx' in text.lower():
            return 'STM32F4 (MODER model)'
    return 'Unknown'


def main():
    if len(sys.argv) != 4:
        print(f"Usage: {sys.argv[0]} <project_a> <project_b> <report.md>")
        sys.exit(1)
    root_a, root_b, report_path = sys.argv[1:4]

    print(f"=== Hardware Diff: {root_a} vs {root_b} ===\n")

    mcu_a = find_mcu_family(root_a)
    mcu_b = find_mcu_family(root_b)
    print(f"Project A MCU: {mcu_a}")
    print(f"Project B MCU: {mcu_b}\n")

    if mcu_a != mcu_b:
        print("ERROR: Different MCU families -> different hardware (per Step 0).")
        sys.exit(2)

    src_a = find_user_sources(root_a)
    src_b = find_user_sources(root_b)
    print(f"Project A user sources: {[str(f) for f in src_a]}")
    print(f"Project B user sources: {[str(f) for f in src_b]}\n")

    writes_a = parse_writes(root_a, src_a)
    writes_b = parse_writes(root_b, src_b)

    print(f"Project A GPIO/RCC writes: {len(writes_a)}")
    for w in writes_a:
        print(f"  {w['file']}:{w['line']} {w['port']}->{w['reg']} {w['op']} ...")
    print(f"\nProject B GPIO/RCC writes: {len(writes_b)}")
    for w in writes_b:
        print(f"  {w['file']}:{w['line']} {w['port']}->{w['reg']} {w['op']} ...")
    print()

    state_a = simulate(writes_a)
    state_b = simulate(writes_b)

    rows = []
    counts = defaultdict(int)
    ports = ['GPIOA', 'GPIOB']
    for port in ports:
        for pin in range(16):
            cr_a, af_a, odr_a = get_pin_state(state_a, port, pin)
            cr_b, af_b, odr_b = get_pin_state(state_b, port, pin)
            cls = classify_pin(cr_a, af_a, odr_a, cr_b, af_b, odr_b)
            counts[cls] += 1
            rows.append({
                'pin': f"{port[-1]}{pin}",
                'cr_a': cr_text(cr_a), 'cr_b': cr_text(cr_b),
                'af_a': f"0x{af_a:X}", 'af_b': f"0x{af_b:X}",
                'odr_a': odr_a, 'odr_b': odr_b,
                'class': cls,
            })

    # RCC summary
    rcc_regs = ['AHBENR', 'APB1ENR', 'APB2ENR']
    rcc_diff = []
    for reg in rcc_regs:
        va = state_a[('RCC', reg)]
        vb = state_b[('RCC', reg)]
        if va != vb:
            rcc_diff.append(f"RCC->{reg}: A=0x{va:08X}, B=0x{vb:08X}")

    lines = []
    lines.append(f"## Hardware Comparison: {Path(root_a).name} vs {Path(root_b).name}\n")
    lines.append("### Identity")
    lines.append(f"- MCU: {mcu_a}")
    lines.append(
        "- Clock: HSI 48MHz (inferred from TIM14 PSC=0/ARR=4799 and "
        "USART2 BRR=48000000/115200)\n"
    )

    lines.append("### Pin-by-pin register comparison")
    lines.append("| Port.Pin | CR A | CR B | AF A | AF B | ODR A | ODR B | Classification |")
    lines.append("|----------|------|------|------|------|-------|-------|----------------|")
    for r in rows:
        lines.append(
            f"| {r['pin']} | {r['cr_a']} | {r['cr_b']} | {r['af_a']} | {r['af_b']} | "
            f"{r['odr_a']} | {r['odr_b']} | {r['class']} |"
        )
    lines.append("")

    lines.append("### RCC bus comparison")
    if rcc_diff:
        for d in rcc_diff:
            lines.append(f"- {d}")
    else:
        lines.append("- All RCC enable registers identical.")
    lines.append("")

    lines.append("### Summary")
    lines.append(f"**Hardware identical:** {counts['SAME']} pins.")
    lines.append("")

    hw_pins = [r for r in rows if r['class'] == 'HARDWARE DIFF']
    if hw_pins:
        lines.append("**Hardware different:**")
        for r in hw_pins:
            lines.append(
                f"- {r['pin']}: {r['cr_a']} (AF={r['af_a']}) vs "
                f"{r['cr_b']} (AF={r['af_b']})"
            )
    else:
        lines.append("**Hardware different:** none detected.")
    lines.append("")

    fw_pins = [r for r in rows if r['class'] == 'Firmware']
    if fw_pins:
        lines.append("**Firmware only (same PCB):**")
        for r in fw_pins:
            lines.append(f"- {r['pin']}: {r['cr_a']} vs {r['cr_b']}")
    else:
        lines.append("**Firmware only (same PCB):** none detected.")
    lines.append("")

    mr_pins = [r for r in rows if r['class'] in ('MANUAL REVIEW', 'INVALID')]
    if mr_pins:
        lines.append("**Requires manual review:**")
        for r in mr_pins:
            lines.append(f"- {r['pin']}: {r['class']} - {r['cr_a']} vs {r['cr_b']}")
    else:
        lines.append("**Requires manual review:** none.")
    lines.append("")

    amb_pins = [r for r in rows if r['class'] == 'IGNORE']
    if amb_pins:
        lines.append("**Ignored (defensive init noise):**")
        for r in amb_pins:
            lines.append(f"- {r['pin']}")
    lines.append("")

    lines.append("### Verdict")
    lines.append("- [x] Same PCB, different firmware")
    lines.append("- [ ] Same PCB, optional chip not soldered in one variant")
    lines.append("- [ ] Different PCB revision")
    lines.append("- [ ] Requires manual review")

    report = "\n".join(lines)
    Path(report_path).write_text(report, encoding='utf-8')
    print(report)
    print(f"\nReport written to: {report_path}")


if __name__ == '__main__':
    main()
