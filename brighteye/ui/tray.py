"""Windows 系统托盘图标（右下角通知区域）· 纯 ctypes Shell_NotifyIcon，零新依赖。

商业软件的常规做法：进程常驻时在任务栏通知区域放一枚托盘图标，
「关闭窗口=收起、托盘=进程仍在」的心智由托盘图标承接。
本模块不用 pystray（避免新增第三方依赖，遵守离线优先铁律），
直接调 Win32 API：

  1. 独立 daemon 线程里注册一个隐藏消息窗口 + GetMessage 消息泵
     （tkinter 已占用主线程消息循环，托盘必须自带线程）；
  2. Shell_NotifyIcon(NIM_ADD) 挂图标（.ico 优先，缺失用系统默认应用图标）；
  3. 左键单击 → 派发 "open"；右键 → TrackPopupMenu 弹菜单，
     菜单项由 menu_provider 回调实时生成（当前模式打勾）；
  4. 选中项通过线程安全 queue 交回 tkinter 主循环执行（不跨线程碰 UI）；
  5. 监听 TaskbarCreated：explorer.exe 崩溃重启后自动补挂图标；
  6. 任何失败（非 Windows / API 异常）→ available()=False，静默降级，
     绝不影响主程序（铁律：可选增强，失败不阻塞）。
"""

from __future__ import annotations

import queue
import sys
import threading
from typing import Callable, List, Optional, Tuple

_IS_WINDOWS = sys.platform == "win32"

# —— Win32 常量 ——
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_LBUTTONUP = 0x0202
WM_RBUTTONUP = 0x0205
WM_USER = 0x0400
WM_TRAY = WM_USER + 20          # 托盘回调消息（自定义）

NIM_ADD = 0x0
NIM_MODIFY = 0x1
NIM_DELETE = 0x2
NIF_MESSAGE = 0x1
NIF_ICON = 0x2
NIF_TIP = 0x4

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040
IDI_APPLICATION = 32512

MF_STRING = 0x0000
MF_SEPARATOR = 0x0800
MF_CHECKED = 0x0008
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080


class TrayIcon:
    """系统托盘图标。menu_provider() → [(key, label, checked)]，key=None 为分隔线。

    动作 key 经内部队列交回主线程：UI 侧周期调用 poll() 取出执行。
    """

    def __init__(self, tooltip: str, icon_path: Optional[str] = None,
                 menu_provider: Optional[Callable[
                     [], List[Tuple[Optional[str], str, bool]]]] = None,
                 default_action: str = "open"):
        self._tooltip = tooltip[:127]
        self._icon_path = icon_path
        self._menu_provider = menu_provider or (lambda: [])
        self._default_action = default_action
        self._actions: "queue.Queue[str]" = queue.Queue()
        self._hwnd = None
        self._hicon = None
        self._ok = False
        self._stop = threading.Event()
        if _IS_WINDOWS:
            self._thread = threading.Thread(
                target=self._run, daemon=True, name="brighteye-tray")
            self._thread.start()

    # ---- 主线程侧接口 -------------------------------------------------
    def available(self) -> bool:
        return self._ok

    def poll(self) -> List[str]:
        """取出用户在托盘上触发的全部动作 key（主线程周期调用）。"""
        out = []
        while True:
            try:
                out.append(self._actions.get_nowait())
            except queue.Empty:
                return out

    def close(self) -> None:
        """摘掉托盘图标并结束消息线程（幂等，退出时调用）。"""
        if not (_IS_WINDOWS and self._hwnd):
            self._stop.set()
            return
        try:
            import ctypes
            self._stop.set()
            # 请求消息窗口关闭 → 消息泵线程内做 NIM_DELETE + 退出
            ctypes.windll.user32.PostMessageW(self._hwnd, WM_CLOSE, 0, 0)
        except Exception:
            pass

    # ---- 托盘线程：消息窗口 + 消息泵 ----------------------------------
    def _run(self) -> None:
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            shell32 = ctypes.windll.shell32
            kernel32 = ctypes.windll.kernel32

            WNDPROC = ctypes.WINFUNCTYPE(
                ctypes.c_longlong, wintypes.HWND, ctypes.c_uint,
                wintypes.WPARAM, wintypes.LPARAM)

            class NOTIFYICONDATAW(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.DWORD),
                    ("hWnd", wintypes.HWND),
                    ("uID", wintypes.UINT),
                    ("uFlags", wintypes.UINT),
                    ("uCallbackMessage", wintypes.UINT),
                    ("hIcon", wintypes.HICON),
                    ("szTip", wintypes.WCHAR * 128),
                    ("dwState", wintypes.DWORD),
                    ("dwStateMask", wintypes.DWORD),
                    ("szInfo", wintypes.WCHAR * 256),
                    ("uVersion", wintypes.UINT),
                    ("szInfoTitle", wintypes.WCHAR * 64),
                    ("dwInfoFlags", wintypes.DWORD),
                ]

            taskbar_created = user32.RegisterWindowMessageW("TaskbarCreated")
            cmd_map = {}          # 菜单命令 id → action key（每次弹菜单前重建）

            def add_icon(hwnd):
                nid = NOTIFYICONDATAW()
                nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
                nid.hWnd = hwnd
                nid.uID = 1
                nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
                nid.uCallbackMessage = WM_TRAY
                nid.hIcon = self._hicon
                nid.szTip = self._tooltip
                shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
                return nid

            def show_menu(hwnd):
                """右键弹出上下文菜单，返回被选中的 action key（未选中=None）。"""
                items = []
                try:
                    items = self._menu_provider() or []
                except Exception:
                    pass
                if not items:
                    return None
                hmenu = user32.CreatePopupMenu()
                cmd_map.clear()
                cid = 1000
                for key, label, checked in items:
                    if key is None:
                        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                        continue
                    cid += 1
                    cmd_map[cid] = key
                    flags = MF_STRING | (MF_CHECKED if checked else 0)
                    user32.AppendMenuW(hmenu, flags, cid, label)
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                # 官方要求：弹菜单前 SetForegroundWindow，否则点外面菜单不消失
                user32.SetForegroundWindow(hwnd)
                cmd = user32.TrackPopupMenu(
                    hmenu, TPM_RETURNCMD | TPM_NONOTIFY,
                    pt.x, pt.y, 0, hwnd, None)
                user32.PostMessageW(hwnd, 0, 0, 0)   # 官方建议的收尾消息
                user32.DestroyMenu(hmenu)
                return cmd_map.get(cmd)

            def wnd_proc(hwnd, msg, wparam, lparam):
                if msg == WM_TRAY:
                    if lparam == WM_LBUTTONUP:
                        self._actions.put(self._default_action)
                    elif lparam == WM_RBUTTONUP:
                        key = show_menu(hwnd)
                        if key:
                            self._actions.put(key)
                    return 0
                if msg == taskbar_created:
                    # explorer 重启 → 托盘区被清空，补挂图标
                    try:
                        add_icon(hwnd)
                    except Exception:
                        pass
                    return 0
                if msg == WM_CLOSE:
                    user32.DestroyWindow(hwnd)
                    return 0
                if msg == WM_DESTROY:
                    user32.PostQuitMessage(0)
                    return 0
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

            # DefWindowProcW 返回值声明为 64 位，避免高位截断
            user32.DefWindowProcW.restype = ctypes.c_longlong
            user32.DefWindowProcW.argtypes = [
                wintypes.HWND, ctypes.c_uint,
                wintypes.WPARAM, wintypes.LPARAM]

            proc = WNDPROC(wnd_proc)      # 保持引用防 GC

            class WNDCLASSW(ctypes.Structure):
                _fields_ = [
                    ("style", ctypes.c_uint),
                    ("lpfnWndProc", WNDPROC),
                    ("cbClsExtra", ctypes.c_int),
                    ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE),
                    ("hIcon", wintypes.HICON),
                    ("hCursor", ctypes.c_void_p),
                    ("hbrBackground", ctypes.c_void_p),
                    ("lpszMenuName", wintypes.LPCWSTR),
                    ("lpszClassName", wintypes.LPCWSTR),
                ]

            hinst = kernel32.GetModuleHandleW(None)
            wc = WNDCLASSW()
            wc.lpfnWndProc = proc
            wc.hInstance = hinst
            wc.lpszClassName = "BrightEyeTrayWnd"
            user32.RegisterClassW(ctypes.byref(wc))
            hwnd = user32.CreateWindowExW(
                0, wc.lpszClassName, "BrightEyeTray", 0,
                0, 0, 0, 0, None, None, hinst, None)
            if not hwnd:
                return
            self._hwnd = hwnd

            # 图标：.ico 文件优先；缺失/加载失败 → 系统默认应用图标
            hicon = None
            if self._icon_path:
                try:
                    hicon = user32.LoadImageW(
                        None, self._icon_path, IMAGE_ICON, 0, 0,
                        LR_LOADFROMFILE | LR_DEFAULTSIZE)
                except Exception:
                    hicon = None
            if not hicon:
                hicon = user32.LoadIconW(None, IDI_APPLICATION)
            self._hicon = hicon

            nid = add_icon(hwnd)
            self._ok = True

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))

            # 消息泵退出 → 摘图标
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            except Exception:
                pass
        except Exception:
            self._ok = False   # 任意失败静默降级：无托盘但主程序照常
        finally:
            self._hwnd = None
