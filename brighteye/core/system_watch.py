"""深度系统集成（建议4落地）：全屏游戏/专注识别 + 显示器物理亮度调节。

一、FullscreenWatcher —— 游戏/专注模式识别
    通过 Windows API（纯 ctypes，零依赖）判断前台窗口是否全屏独占：
    GetForegroundWindow → GetWindowRect 与所在显示器分辨率比对，
    并排除桌面壳窗口(Progman/WorkerW)。检测到全屏（竞技游戏/放映/
    专注写作等）时，monitor 自动进入「游戏勿扰」：不弹台词、不弹窗、
    暂缓强制干预，避免团战关头弹遮罩的糟糕体验；数据仍后台记录。
    非 Windows 平台恒返回 False（功能自动关闭）。

二、BrightnessController —— DDC/CI 物理亮度
    软导入第三方库 monitorcontrol（pip install monitorcontrol，可选），
    通过 DDC/CI 协议调节外接显示器物理亮度：强制休息遮罩期间调暗、
    休息结束恢复，保护效果比纯软件遮罩更彻底。未安装该库或显示器
    不支持 DDC/CI 时自动降级为 no-op，不影响其余功能。
"""

from __future__ import annotations

import sys
import time
from typing import Optional

_IS_WIN = sys.platform == "win32"

# 桌面壳窗口类名：本身就是"全屏"的桌面，必须排除
_SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd"}


class FullscreenWatcher:
    """节流地检测前台窗口是否全屏独占（游戏/放映/专注场景）。"""

    def __init__(self, poll_sec: float = 2.0):
        self.poll_sec = poll_sec
        self._last_check = 0.0
        self._last_result = False

    def is_fullscreen(self) -> bool:
        """当前前台窗口是否全屏。结果按 poll_sec 节流缓存。"""
        now = time.time()
        if now - self._last_check < self.poll_sec:
            return self._last_result
        self._last_check = now
        self._last_result = self._check() if _IS_WIN else False
        return self._last_result

    @staticmethod
    def _check() -> bool:
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            hwnd = user32.GetForegroundWindow()
            if not hwnd:
                return False

            # 排除桌面壳窗口
            buf = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, buf, 64)
            if buf.value in _SHELL_CLASSES:
                return False

            rect = wintypes.RECT()
            if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                return False

            # 与窗口所在显示器（多屏兼容）的完整分辨率比对
            MONITOR_DEFAULTTONEAREST = 2
            monitor = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", wintypes.DWORD),
                            ("rcMonitor", wintypes.RECT),
                            ("rcWork", wintypes.RECT),
                            ("dwFlags", wintypes.DWORD)]

            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if not user32.GetMonitorInfoW(monitor, ctypes.byref(mi)):
                return False
            mr = mi.rcMonitor
            # 窗口完整覆盖显示器 → 全屏独占（无边框全屏窗口同样命中）
            return (rect.left <= mr.left and rect.top <= mr.top
                    and rect.right >= mr.right and rect.bottom >= mr.bottom)
        except Exception:
            return False


class BrightnessController:
    """DDC/CI 显示器物理亮度控制（依赖可选库 monitorcontrol）。"""

    def __init__(self):
        self._saved: Optional[int] = None    # 调暗前的亮度，供恢复
        try:
            from monitorcontrol import get_monitors  # noqa: F401
            self.available = True
        except Exception:
            self.available = False

    def _each(self, fn) -> bool:
        """对每台支持 DDC/CI 的显示器执行 fn(monitor)；全失败返回 False。"""
        if not self.available:
            return False
        try:
            from monitorcontrol import get_monitors
            ok = False
            for m in get_monitors():
                try:
                    with m:
                        fn(m)
                    ok = True
                except Exception:
                    continue
            return ok
        except Exception:
            return False

    def dim(self, percent: int = 40) -> bool:
        """调暗到指定亮度（记录原值供 restore）。不支持则 no-op。"""
        percent = max(0, min(100, int(percent)))
        state = {}

        def _apply(m):
            if self._saved is None:
                try:
                    state["orig"] = int(m.get_luminance())
                except Exception:
                    pass
            m.set_luminance(percent)

        ok = self._each(_apply)
        if ok and self._saved is None:
            self._saved = state.get("orig", 80)
        return ok

    def restore(self) -> bool:
        """恢复 dim 之前的亮度。"""
        if self._saved is None:
            return False
        saved = self._saved
        ok = self._each(lambda m: m.set_luminance(saved))
        if ok:
            self._saved = None
        return ok
