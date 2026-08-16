"""Spike: find save dialog (#32770) owned by ATK-Logic and dump its control tree.
Pure Win32 messages — no UIA, no focus change.
"""
import ctypes
import ctypes.wintypes as wt
import sys

user32 = ctypes.windll.user32


def gui_pid():
    hwnd = user32.FindWindowW("Qt5152QWindowOwnDCIcon", "ATK-LogicView - DL16")
    if not hwnd:
        hwnd = user32.FindWindowW(None, "ATK-LogicView - DL16")
    if not hwnd:
        return None, None
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return hwnd, pid.value


def text_of(h, n=256):
    buf = ctypes.create_unicode_buffer(n)
    user32.GetWindowTextW(h, buf, n - 1)
    return buf.value


def class_of(h, n=64):
    buf = ctypes.create_unicode_buffer(n)
    user32.GetClassNameW(h, buf, n - 1)
    return buf.value


def find_dialogs(pid):
    out = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(h, lp):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(h, ctypes.byref(p))
        if p.value == pid and class_of(h) == "#32770" and user32.IsWindowVisible(h):
            out.append(h)
        return True

    user32.EnumWindows(cb, 0)
    return out


def dump_children(h, indent=2):
    kids = []

    @ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    def cb(c, lp):
        kids.append(c)
        return True

    user32.EnumChildWindows(h, cb, 0)
    for c in kids:
        cid = user32.GetDlgCtrlID(c)
        vis = user32.IsWindowVisible(c)
        print(f"{' ' * indent}hwnd={hex(c)} cls='{class_of(c)}' "
              f"id={cid} vis={vis} text='{text_of(c)[:40]}'")
        # recurse one level for ComboBoxEx32 -> ComboBox -> Edit chains
        if class_of(c) in ("ComboBoxEx32", "ComboBox"):
            dump_children(c, indent + 4)


def main():
    ghwnd, pid = gui_pid()
    if not pid:
        print("GUI not found")
        sys.exit(1)
    print(f"GUI hwnd={hex(ghwnd)} pid={pid}")
    dlgs = find_dialogs(pid)
    print(f"#32770 dialogs: {len(dlgs)}")
    for d in dlgs:
        print(f"  dialog hwnd={hex(d)} title='{text_of(d)}'")
        dump_children(d)


if __name__ == "__main__":
    main()
