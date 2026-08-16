// atk_bg.exe — non-disturbing ATK-Logic driver.
//
// Background-message helper for atk_cli. Replaces the previous proxy-DLL
// path: PostMessage + SendMessage to the official Qt GUI window instead of
// injection. Zero keyboard/mouse focus, zero process injection.
//
// Subcommands:
//   atk_bg.exe key F1|F2|F12
//       Find the ATK-LogicView window, fake-activate (WA_ACTIVE +
//       WM_SETFOCUS, not real foreground), then PostMessage WM_KEYDOWN/WM_KEYUP
//       for the requested function key. The GUI processes its hotkey normally
//       (set.ini defines startCollecting=F1, stopCollecting=F2, saveFile=F12).
//
//   atk_bg.exe save <full-path> [--timeout N]
//       Wait for the #32770 Save-As dialog to appear (owned by ATK-Logic
//       process). Walk ComboBoxEx32 -> ComboBox -> Edit (first Edit). Send
//       WM_SETTEXT with the path. Click the save Button (id=1 or text
//       "保存"/"Save"). Handle overwrite confirm.
//
// All output is JSON on stdout. Errors: non-zero exit + JSON error.
//
// C# 5 syntax only (no $"interpolation") — csc.exe in v4.0.30319 builds
// with C# 5 by default. Use string.Format() and concatenation instead.
//
// Compile:
//   C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /o+
//       /out:atk_bg.exe atk_bg.cs

using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;

namespace atk_bg
{
    internal static class Win32
    {
        // Win32 messages
        public const uint WM_KEYDOWN       = 0x0100;
        public const uint WM_KEYUP         = 0x0101;
        public const uint WM_ACTIVATE      = 0x0006;
        public const uint WM_SETFOCUS      = 0x0007;
        public const uint WM_KILLFOCUS     = 0x0008;
        public const uint WM_SETTEXT       = 0x000C;
        public const uint BM_CLICK         = 0x00F5;

        public const int  WA_ACTIVE        = 1;
        public const int  WA_INACTIVE      = 0;

        // Virtual key codes
        public const int  VK_F1            = 0x70;
        public const int  VK_F2            = 0x71;
        public const int  VK_F3            = 0x72;
        public const int  VK_F4            = 0x73;
        public const int  VK_F5            = 0x74;
        public const int  VK_F6            = 0x75;
        public const int  VK_F7            = 0x76;
        public const int  VK_F8            = 0x77;
        public const int  VK_F9            = 0x78;
        public const int  VK_F10           = 0x79;
        public const int  VK_F11           = 0x7A;
        public const int  VK_F12           = 0x7B;

        [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern IntPtr FindWindowW(string lpClassName, string lpWindowName);

        [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Auto)]
        public static extern IntPtr FindWindowEx(IntPtr hwndParent, IntPtr hwndChildAfter, string lpszClass, string lpszWindow);

        [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern int GetWindowTextW(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

        [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
        public static extern int GetClassNameW(IntPtr hWnd, StringBuilder lpString, int nMaxCount);

        [DllImport("user32.dll", SetLastError = true)]
        public static extern uint GetWindowThreadProcessId(IntPtr hWnd, out uint lpdwProcessId);

        [DllImport("user32.dll", SetLastError = true)]
        public static extern bool IsWindowVisible(IntPtr hWnd);

        [DllImport("user32.dll", SetLastError = true)]
        public static extern bool EnumWindows(EnumWindowsProc lpEnumFunc, IntPtr lParam);

        [DllImport("user32.dll", SetLastError = true)]
        public static extern bool EnumChildWindows(IntPtr hWndParent, EnumWindowsProc lpEnumFunc, IntPtr lParam);

        [DllImport("user32.dll", SetLastError = true)]
        public static extern int GetDlgCtrlID(IntPtr hWnd);

        [DllImport("user32.dll")]
        public static extern uint MapVirtualKeyW(uint uCode, uint uMapType);

        [DllImport("user32.dll")]
        [return: MarshalAs(UnmanagedType.Bool)]
        public static extern bool PostMessageW(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

        [DllImport("user32.dll")]
        public static extern IntPtr SendMessageW(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);

        public delegate bool EnumWindowsProc(IntPtr hWnd, IntPtr lParam);
    }

    internal class Program
    {
        private const string GUI_CLASS = "Qt5152QWindowOwnDCIcon";
        private const string GUI_TITLE  = "ATK-LogicView - DL16";

        // ─────────────────────────────────────────────────────────────────
        // Window helpers
        // ─────────────────────────────────────────────────────────────────

        static IntPtr FindGuiWindow()
        {
            IntPtr h = Win32.FindWindowW(GUI_CLASS, GUI_TITLE);
            if (h != IntPtr.Zero) return h;
            return Win32.FindWindowW(null, GUI_TITLE);
        }

        static uint PidOf(IntPtr h)
        {
            uint pid;
            Win32.GetWindowThreadProcessId(h, out pid);
            return pid;
        }

        static string TextOf(IntPtr h, int n)
        {
            var sb = new StringBuilder(n);
            Win32.GetWindowTextW(h, sb, n);
            return sb.ToString();
        }

        static string ClassOf(IntPtr h)
        {
            var sb = new StringBuilder(64);
            Win32.GetClassNameW(h, sb, 64);
            return sb.ToString();
        }

        static IntPtr LParamFor(int vk, bool up)
        {
            // MAPVK_VK_TO_VSC = 0. Build lparam: repeat=0, scan code in bits 16-23,
            // extended=0, key-was-down not from previous state not tracked (0 in bit 30).
            // Up state sets bit 30 (was previous) + bit 31 (transition).
            uint sc = Win32.MapVirtualKeyW((uint)vk, 0);
            long lp = 1L | ((long)sc << 16);
            if (up) lp |= (1L << 30) | (1L << 31);
            return (IntPtr)lp;
        }

        // ─────────────────────────────────────────────────────────────────
        // JSON helpers (C# 5 — no $"interpolation")
        // ─────────────────────────────────────────────────────────────────

        static string Jstr(string s)
        {
            if (s == null) return "";
            var sb = new StringBuilder(s.Length + 8);
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"':  sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    default:
                        if (c < 0x20)
                            sb.AppendFormat(CultureInfo.InvariantCulture, "\\u{0:x4}", (int)c);
                        else
                            sb.Append(c);
                        break;
                }
            }
            return sb.ToString();
        }

        static void EmitOk(string cmd, string body)
        {
            Console.Out.WriteLine("{\"status\":\"ok\",\"cmd\":\"" + cmd + "\"" + body + "}");
        }

        static void EmitErr(string cmd, string msg)
        {
            Console.Error.WriteLine("{\"status\":\"error\",\"cmd\":\"" + cmd + "\",\"msg\":\"" + Jstr(msg) + "\"}");
        }

        // ─────────────────────────────────────────────────────────────────
        // key F1..F12
        // ─────────────────────────────────────────────────────────────────

        static int DoKey(string keyName)
        {
            int vk;
            string upKey;
            switch (keyName.ToUpperInvariant())
            {
                case "F1":  vk = Win32.VK_F1;  upKey = "F1"; break;
                case "F2":  vk = Win32.VK_F2;  upKey = "F2"; break;
                case "F3":  vk = Win32.VK_F3;  upKey = "F3"; break;
                case "F4":  vk = Win32.VK_F4;  upKey = "F4"; break;
                case "F5":  vk = Win32.VK_F5;  upKey = "F5"; break;
                case "F6":  vk = Win32.VK_F6;  upKey = "F6"; break;
                case "F7":  vk = Win32.VK_F7;  upKey = "F7"; break;
                case "F8":  vk = Win32.VK_F8;  upKey = "F8"; break;
                case "F9":  vk = Win32.VK_F9;  upKey = "F9"; break;
                case "F10": vk = Win32.VK_F10; upKey = "F10"; break;
                case "F11": vk = Win32.VK_F11; upKey = "F11"; break;
                case "F12": vk = Win32.VK_F12; upKey = "F12"; break;
                default:
                    EmitErr("key", "unsupported key: " + keyName);
                    return 2;
            }

            IntPtr hwnd = FindGuiWindow();
            if (hwnd == IntPtr.Zero)
            {
                EmitErr("key", "ATK-LogicView window not found");
                return 3;
            }

            // Fake-activate so Qt treats the window as active and processes
            // shortcuts. We do NOT bring it to the foreground — PostMessage
            // is queue-send, focus stays with user's app.
            Win32.PostMessageW(hwnd, Win32.WM_ACTIVATE, (IntPtr)Win32.WA_ACTIVE, IntPtr.Zero);
            Win32.PostMessageW(hwnd, Win32.WM_SETFOCUS, IntPtr.Zero, IntPtr.Zero);
            Thread.Sleep(50);

            Win32.PostMessageW(hwnd, Win32.WM_KEYDOWN, (IntPtr)vk, LParamFor(vk, false));
            Thread.Sleep(50);
            Win32.PostMessageW(hwnd, Win32.WM_KEYUP,   (IntPtr)vk, LParamFor(vk, true));

            EmitOk("key", ",\"key\":\"" + upKey + "\",\"hwnd\":" + ((long)hwnd).ToString(CultureInfo.InvariantCulture));
            return 0;
        }

        // ─────────────────────────────────────────────────────────────────
        // save <path>
        // ─────────────────────────────────────────────────────────────────

        static List<IntPtr> FindDialogs(uint pid)
        {
            var found = new List<IntPtr>();
            Win32.EnumWindows(delegate(IntPtr h, IntPtr lp)
            {
                uint p;
                Win32.GetWindowThreadProcessId(h, out p);
                if (p == pid && ClassOf(h) == "#32770" && Win32.IsWindowVisible(h))
                    found.Add(h);
                return true;
            }, IntPtr.Zero);
            return found;
        }

        // Walk children looking for the first Edit. Prefer the one nested
        // inside ComboBoxEx32/ComboBox (typical Windows Save-As picker).
        // Returns IntPtr.Zero if none found.
        static IntPtr FindEdit(IntPtr dlg)
        {
            IntPtr result = IntPtr.Zero;

            Win32.EnumChildWindows(dlg, delegate(IntPtr h, IntPtr lp)
            {
                if (result != IntPtr.Zero) return false;
                string cls = ClassOf(h);
                if (cls == "Edit")
                {
                    result = h;
                    return false;  // stop
                }
                if (cls == "ComboBoxEx32" || cls == "ComboBox")
                {
                    Win32.EnumChildWindows(h, delegate(IntPtr sub, IntPtr lp2)
                    {
                        if (ClassOf(sub) == "Edit")
                        {
                            result = sub;
                            return false;
                        }
                        return true;
                    }, IntPtr.Zero);
                    if (result != IntPtr.Zero) return false;
                }
                return true;
            }, IntPtr.Zero);

            return result;
        }

        static List<IntPtr> Children(IntPtr parent)
        {
            var kids = new List<IntPtr>();
            Win32.EnumChildWindows(parent, delegate(IntPtr h, IntPtr lp)
            {
                kids.Add(h);
                return true;
            }, IntPtr.Zero);
            return kids;
        }

        // Find Save button: by id=1 or text contains "保存"/"Save".
        static IntPtr FindSaveButton(IntPtr dlg)
        {
            foreach (var c in Children(dlg))
            {
                if (ClassOf(c) != "Button") continue;
                int id = Win32.GetDlgCtrlID(c);
                string txt = TextOf(c, 128);
                if (id == 1 ||
                    txt.IndexOf("保存") >= 0 || txt.IndexOf("Save") >= 0 ||
                    txt.IndexOf("(&S)") >= 0)
                    return c;
            }
            return IntPtr.Zero;
        }

        // Find "Yes" / overwrite confirm button: id=6 or text contains
        // "是"/"替换"/"Yes".
        static IntPtr FindConfirmButton(IntPtr dlg)
        {
            foreach (var c in Children(dlg))
            {
                if (ClassOf(c) != "Button") continue;
                int id = Win32.GetDlgCtrlID(c);
                string txt = TextOf(c, 128);
                if (id == 6 || id == 1 ||
                    txt.IndexOf("是") >= 0 || txt.IndexOf("替换") >= 0 ||
                    txt.IndexOf("Yes") >= 0 || txt.IndexOf("Replace") >= 0)
                    return c;
            }
            return IntPtr.Zero;
        }

        static int DoSave(string path, double timeoutSec)
        {
            // Normalise: ATK-Logic's native #32770 Save picker rejects forward
            // slashes ("提示文件名无效"). We treat input as either '/' or '\',
            // but send backslash form into the dialog. Filename only (no dir)
            // is also accepted; in that case the GUI prepends its savePath
            // from set.ini.
            string savePath = path.Replace('/', '\\');
            var tTotal = System.Diagnostics.Stopwatch.StartNew();
            long tDialogSeenMs = 0, tFillMs = 0, tClickMs = 0;

            IntPtr gui = FindGuiWindow();
            if (gui == IntPtr.Zero)
            {
                EmitErr("save", "ATK-LogicView window not found");
                return 3;
            }
            uint pid = PidOf(gui);

            // Two save modes:
            //   (a) "Save As" picker: GUI opens #32770, we fill name + click.
            //   (b) Direct save: GUI saves to set.ini savePath with last-used
            //       filename; no picker appears. atk_cli can supply the
            //       expected path so we can verify file lands.

            // Cap the outer dialog search so a misbehaving GUI can't pin us
            // here forever.  GUI log shows the picker appears ~500 ms after
            // F12 — 2.5 s is comfortable, 8 s (previous) was overkill.
            double outerCap = timeoutSec < 2.5 ? timeoutSec : 2.5;

            // Poll at 30 ms — fast enough to react to the dialog the instant
            // it appears, slow enough not to burn CPU.  Each call to
            // EnumWindows on a 200-window desktop is cheap.
            const int pollMs = 30;

            IntPtr dlg = IntPtr.Zero;
            var sw = System.Diagnostics.Stopwatch.StartNew();
            while (sw.Elapsed.TotalSeconds < outerCap)
            {
                var dlgs = FindDialogs(pid);
                if (dlgs.Count > 0) { dlg = dlgs[0]; break; }
                // Early-exit if file already exists (mode b did the write).
                if (File.Exists(savePath)) { break; }
                Thread.Sleep(pollMs);
            }

            if (dlg != IntPtr.Zero)
            {
                // ── Mode (a): Save-As picker ──
                IntPtr edit = FindEdit(dlg);
                if (edit == IntPtr.Zero)
                {
                    EmitErr("save", "filename Edit control not found in dialog");
                    return 5;
                }

                // Set filename via WM_SETTEXT. WM_SETTEXT lparam is a pointer
                // to a null-terminated Unicode string.  SendMessage (not
                // PostMessage) so the queue is drained before we click save.
                IntPtr pText = Marshal.StringToHGlobalUni(savePath);
                try
                {
                    Win32.SendMessageW(edit, Win32.WM_SETTEXT, IntPtr.Zero, pText);
                }
                finally
                {
                    Marshal.FreeHGlobal(pText);
                }
                // 30 ms lets Qt's text-edit slot pick up the new value
                // before BM_CLICK triggers its slot — without it the picker
                // saves the OLD name.
                Thread.Sleep(30);
                tFillMs = tTotal.ElapsedMilliseconds;

                IntPtr saveBtn = FindSaveButton(dlg);
                if (saveBtn == IntPtr.Zero)
                {
                    EmitErr("save", "save Button not found");
                    return 6;
                }
                Win32.SendMessageW(saveBtn, Win32.BM_CLICK, IntPtr.Zero, IntPtr.Zero);
            }
            // else: dialog never showed — Mode (b) direct save. The GUI did
            //       the write when F12 was processed; we just poll for the
            //       file to land.

            // After BM_CLICK (mode a) or after F12 (mode b) the GUI may show
            // a brief overwrite-confirm #32770 ("Use selected format") or
            // overwrite silently.  The file actually lands on disk
            // asynchronously — poll until it appears.
            //
            // Cap at 1.5 s: a 47 KB .atkdl write completes in ~50–100 ms on
            // local SSD; 1.5 s leaves a 15× margin while shaving 2 s off the
            // previous 3 s cap.
            bool ok = false;
            long size = 0L;
            var sw2 = System.Diagnostics.Stopwatch.StartNew();
            while (sw2.Elapsed.TotalSeconds < 1.5)
            {
                // Pick first non-original-dialog #32770 and BM_CLICK any
                // "Yes"-style button on it (id=6 or text contains 是/替换).
                foreach (var d in FindDialogs(pid))
                {
                    if (d == dlg) continue;
                    IntPtr yes = FindConfirmButton(d);
                    if (yes != IntPtr.Zero)
                    {
                        Win32.SendMessageW(yes, Win32.BM_CLICK, IntPtr.Zero, IntPtr.Zero);
                    }
                }

                if (File.Exists(savePath))
                {
                    ok = true;
                    try { size = new FileInfo(savePath).Length; }
                    catch { size = 0L; }
                    break;
                }
                Thread.Sleep(pollMs);
            }
            string body = ",\"path\":\"" + Jstr(path) + "\",\"sent_path\":\"" + Jstr(savePath)
                + "\",\"saved\":" + (ok ? "true" : "false")
                + ",\"size\":" + size.ToString(CultureInfo.InvariantCulture);
            EmitOk("save", body);
            return ok ? 0 : 7;
        }

        // ─────────────────────────────────────────────────────────────────
        // main
        // ─────────────────────────────────────────────────────────────────

        static int Main(string[] args)
        {
            try
            {
                if (args.Length < 1)
                {
                    Console.Error.WriteLine("usage: atk_bg.exe key F1|F2|F12 | save <path> [--timeout N]");
                    return 1;
                }
                string cmd = args[0].ToLowerInvariant();

                if (cmd == "key")
                {
                    if (args.Length < 2)
                    {
                        Console.Error.WriteLine("{\"status\":\"error\",\"msg\":\"key requires F1..F12 argument\"}");
                        return 1;
                    }
                    return DoKey(args[1]);
                }
                if (cmd == "save")
                {
                    if (args.Length < 2)
                    {
                        Console.Error.WriteLine("{\"status\":\"error\",\"msg\":\"save requires a path argument\"}");
                        return 1;
                    }
                    double t = 8.0;
                    for (int i = 2; i + 1 < args.Length; i++)
                    {
                        if (args[i] == "--timeout")
                        {
                            double.TryParse(args[i + 1], NumberStyles.Float,
                                CultureInfo.InvariantCulture, out t);
                        }
                    }
                    return DoSave(args[1], t);
                }

                Console.Error.WriteLine("{\"status\":\"error\",\"msg\":\"unknown command '" + cmd + "'\"}");
                return 1;
            }
            catch (Exception ex)
            {
                Console.Error.WriteLine("{\"status\":\"error\",\"exception\":\""
                    + Jstr(ex.GetType().Name) + "\",\"msg\":\"" + Jstr(ex.Message) + "\"}");
                return 99;
            }
        }
    }
}
