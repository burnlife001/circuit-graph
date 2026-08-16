"""Spike E2E: full capture chain via background PostMessage only.

F1 (start) -> 3s -> F2 (stop) -> Ctrl+S -> fill Save-As dialog via
WM_SETTEXT + BM_CLICK (no focus, no keyboard, no mouse).

Ground truth: .atkdl file appears on disk.
Also verifies foreground window never changes.
"""
import ctypes
import ctypes.wintypes as wt
import os
import sys
import time

user32 = ctypes.windll.user32

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_ACTIVATE, WM_SETFOCUS, WM_KILLFOCUS = 0x0006, 0x0007, 0x0008
WM_SETTEXT = 0x000C
BM_CLICK = 0x00F5
WA_ACTIVE, WA_INACTIVE = 1, 0
VK_F1, VK_F2, VK_CONTROL, VK_S = 0x70, 0x71, 0x11, 0x53

OUT = r"E:\__electric\atk-logic-data\spike_postmsg.atkdl"


def find_gui():
    hwnd = user32.FindWindowW("Qt5152QWindowOwnDCIcon", "ATK-LogicView - DL16")
    if not hwnd:
        hwnd = user32.FindWindowW(None, "ATK-LogicView - DL16")
    return hwnd


def pid_of(hwnd):
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def lparam(vk, up):
    sc = user32.MapVirtualKeyW(vk, 0)
    lp = 1 | (sc << 16)
    if up:
        lp |= (1 << 30) | (1 << 31)
    return lp


def key(hwnd, vk):
    user32.PostMessageW(hwnd, WM_KEYDOWN, vk, lparam(vk, False))
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_KEYUP, vk, lparam(vk, True))


def combo(hwnd, mod, vk):
    user32.PostMessageW(hwnd, WM_KEYDOWN, mod, lparam(mod, False))
    time.sleep(0.05)
    key(hwnd, vk)
    time.sleep(0.05)
    user32.PostMessageW(hwnd, WM_KEYUP, mod, lparam(mod, True))


def class_of(h):
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(h, buf, 63)
    return buf.value


def text_of(h, n=256):
    buf = ctypes.create_unicode_buffer(n)
    user32.GetWindowTextW(h, buf, n - 1)
    return buf.value


def children(h):
    kids = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(c, lp):
        kids.append(c)
        return True

    user32.EnumChildWindows(h, cb, 0)
    return kids


def find_dialog(pid, timeout=10.0):
    """First visible #32770 owned by pid. Returns hwnd or 0."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        found = []

        @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        def cb(h, lp):
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(h, ctypes.byref(p))
            if p.value == pid and class_of(h) == "#32770" and user32.IsWindowVisible(h):
                found.append(h)
            return True

        user32.EnumWindows(cb, 0)
        if found:
            return found[0]
        time.sleep(0.2)
    return 0


def find_edit(dlg):
    """Filename edit: descend ComboBoxEx32 -> ComboBox -> Edit; fallback any Edit."""
    edits = []

    def scan(h, depth):
        for c in children(h):
            cls = class_of(c)
            if cls == "Edit":
                edits.append(c)
            if cls in ("ComboBoxEx32", "ComboBox") and depth < 4:
                scan(c, depth + 1)

    scan(dlg, 0)
    return edits[0] if edits else 0


def find_button(dlg, want_texts, want_ids=()):
    for c in children(dlg):
        if class_of(c) == "Button":
            cid = user32.GetDlgCtrlID(c)
            t = text_of(c)
            if cid in want_ids or any(w in t for w in want_texts):
                return c, cid, t
    return 0, 0, ""


def main():
    if os.path.exists(OUT):
        os.unlink(OUT)

    hwnd = find_gui()
    if not hwnd:
        print("GUI not found")
        sys.exit(1)
    pid = pid_of(hwnd)
    print(f"GUI hwnd={hex(hwnd)} pid={pid}")
    fg0 = user32.GetForegroundWindow()
    print(f"foreground={hex(fg0)} (gui is background: {fg0 != hwnd})")

    # Fake-activate so Qt thinks the window is active (shortcuts enabled)
    user32.PostMessageW(hwnd, WM_ACTIVATE, WA_ACTIVE, 0)
    user32.PostMessageW(hwnd, WM_SETFOCUS, 0, 0)
    time.sleep(0.2)

    print("posting F1 (start capture)...")
    key(hwnd, VK_F1)
    time.sleep(3.0)
    fg = user32.GetForegroundWindow()
    print(f"  foreground mid-capture: {hex(fg)} unchanged={fg == fg0}")

    print("posting F2 (stop)...")
    key(hwnd, VK_F2)
    time.sleep(3.0)  # GUI processes stop + renders

    print("posting F12 (save, remapped from Ctrl+S in set.ini)...")
    key(hwnd, 0x7B)  # VK_F12

    dlg = find_dialog(pid, timeout=8.0)
    if not dlg:
        print("NO save dialog appeared.")
        print("-> posted keys did NOT trigger the shortcut chain (or direct-save)")
        print(f"-> file exists: {os.path.exists(OUT)}")
        user32.PostMessageW(hwnd, WM_ACTIVATE, WA_INACTIVE, 0)
        sys.exit(2)

    print(f"save dialog: hwnd={hex(dlg)} title='{text_of(dlg)}'")
    for c in children(dlg):
        print(f"   child {class_of(c):14s} id={user32.GetDlgCtrlID(c):5d} '{text_of(c)[:30]}'")

    edit = find_edit(dlg)
    print(f"filename edit: {hex(edit) if edit else 'NOT FOUND'}")
    if not edit:
        sys.exit(3)

    user32.SendMessageW(edit, WM_SETTEXT, 0, OUT)
    time.sleep(0.1)
    got = text_of(edit)
    print(f"edit now reads: '{got[:80]}'")

    btn, cid, btext = find_button(dlg, ["保存", "Save", "(&S)"], want_ids=(1,))
    print(f"save button: {hex(btn) if btn else 'NOT FOUND'} id={cid} '{btext}'")
    if not btn:
        sys.exit(4)

    user32.SendMessageW(btn, BM_CLICK, 0, 0)
    time.sleep(0.8)

    # Overwrite confirm?
    dlg2 = find_dialog(pid, timeout=2.0)
    if dlg2 and dlg2 != dlg:
        print(f"confirm dialog: '{text_of(dlg2)}'")
        yes, yid, ytext = find_button(dlg2, ["是", "Yes", "替换"], want_ids=(6, 1))
        if yes:
            user32.SendMessageW(yes, BM_CLICK, 0, 0)
            print(f"clicked confirm id={yid} '{ytext}'")
        time.sleep(0.5)

    # Deactivate fake activation
    user32.PostMessageW(hwnd, WM_ACTIVATE, WA_INACTIVE, 0)

    fg1 = user32.GetForegroundWindow()
    ok = os.path.exists(OUT)
    size = os.path.getsize(OUT) if ok else 0
    print(f"RESULT: file={'EXISTS ' + str(size) + 'B' if ok else 'MISSING'}  "
          f"foreground unchanged={fg0 == fg1}")
    sys.exit(0 if ok else 5)


if __name__ == "__main__":
    main()
