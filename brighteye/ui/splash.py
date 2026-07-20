"""启动加载页（v1.13.0）—— Q 版弥悠 + 科技感扫描环动画。

视觉后端（mediapipe + 摄像头 + 双模型）在后台线程加载的数秒里，
用一层全窗覆盖 Canvas 作为待机页：中央是 Q 版弥悠头像
（assets/app_icon.png，缺失时程序化矢量兜底），外圈双层旋转弧
扫描环 + 呼吸光点 + 状态文字，风格对齐「弥悠·星夜」主题。

消失时机（由 DashboardApp 驱动）：
  · monitor.backend_name 不再含「加载中」→ 后端就绪，收起；
  · 兜底超时 SPLASH_MAX_SEC 秒后强制收起（绝不卡死界面）；
  · 收起带 6 帧缩圈动画，最短展示 SPLASH_MIN_SEC 秒防"一闪而过"。
零新增依赖：tk.PhotoImage 原生解码 PNG（Tk 8.6+），异常全部静默降级。
"""

from __future__ import annotations

import math
import os
import tkinter as tk

from ..config import CONFIG

_ICON_PATH = os.path.normpath(os.path.join(
    os.path.dirname(__file__), "..", "assets", "app_icon.png"))

SPLASH_MIN_SEC = 1.2     # 最短展示时长(秒)，防止后端秒就绪时一闪而过
SPLASH_MAX_SEC = 25.0    # 兜底超时(秒)：即使后端仍在加载也收起(不挡演示)

# 固定用星夜暗色（加载页=科技感待机，与强制休息遮罩同理不随主题）
_BG = "#0B0620"
_RING1 = "#8E6BFF"       # 外环·弥悠紫
_RING2 = "#1FD6FF"       # 内环·科技青
_ACCENT = "#FF4FB4"      # 光点·粉
_FG = "#F2EDFF"
_MUTED = "#8F86B8"


class SplashOverlay:
    """全窗覆盖的启动待机页。tick() 由主循环 30fps 驱动，request_close()
    请求收起（满足最短展示时长后播放收尾动画并自毁）。"""

    def __init__(self, root: tk.Misc):
        self.alive = True
        self._frame = 0
        self._closing = False
        self._close_req_frame: int | None = None
        self.cv = tk.Canvas(root, bg=_BG, highlightthickness=0, bd=0)
        self.cv.place(x=0, y=0, relwidth=1, relheight=1)
        self.cv.bind("<Configure>", lambda e: self._layout(e.width, e.height))
        # Q 版弥悠头像：PNG 优先，缺失走矢量兜底
        self._avatar: tk.PhotoImage | None = None
        try:
            if os.path.isfile(_ICON_PATH):
                img = tk.PhotoImage(file=_ICON_PATH)          # 512x512
                shrink = max(1, round(img.width() / 168))      # ≈168px
                self._avatar = img.subsample(shrink, shrink)
        except Exception:
            self._avatar = None
        self._w, self._h = 900, 640
        self._layout(self._w, self._h)

    # ---- 布局（窗口尺寸变化时整体重画静态层）------------------------
    def _layout(self, w: int, h: int) -> None:
        if not self.alive:
            return
        self._w, self._h = max(w, 200), max(h, 200)
        cx, cy = self._w / 2, self._h / 2 - 30
        self.cv.delete("all")
        r = 108
        # 头像 / 矢量兜底（猫耳圆头剪影，绝不空窗）
        if self._avatar is not None:
            self.cv.create_image(cx, cy, image=self._avatar)
        else:
            self.cv.create_oval(cx - 60, cy - 55, cx + 60, cy + 65,
                                fill="#D9C6E8", outline=_RING1, width=3)
            self.cv.create_polygon(cx - 55, cy - 35, cx - 78, cy - 88, cx - 18, cy - 60,
                                   fill="#D9C6E8", outline=_RING1)
            self.cv.create_polygon(cx + 55, cy - 35, cx + 78, cy - 88, cx + 18, cy - 60,
                                   fill="#D9C6E8", outline=_RING1)
        # 静态基准圈（暗）
        self.cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                            outline="#221741", width=3)
        # 动态元素：旋转弧 x2 + 呼吸光点，tick() 里只更新这些 tag
        self.cv.create_arc(cx - r, cy - r, cx + r, cy + r, start=0, extent=95,
                           style="arc", outline=_RING1, width=3, tags="arc1")
        r2 = r + 14
        self.cv.create_arc(cx - r2, cy - r2, cx + r2, cy + r2, start=180, extent=55,
                           style="arc", outline=_RING2, width=2, tags="arc2")
        self.cv.create_oval(0, 0, 0, 0, fill=_ACCENT, outline="", tags="dot")
        # 文案
        self.cv.create_text(cx, cy + r + 52, text=CONFIG.app_name,
                            font=("Microsoft YaHei", 20, "bold"), fill=_FG)
        self.cv.create_text(cx, cy + r + 82, text=CONFIG.subtitle,
                            font=("Microsoft YaHei", 10), fill=_MUTED)
        self.cv.create_text(cx, cy + r + 112, text="",
                            font=("Microsoft YaHei", 10), fill=_RING2, tags="status")
        self.cv.create_text(self._w - 14, self._h - 12, anchor="se",
                            text=f"v{CONFIG.version}",
                            font=("Consolas", 9), fill=_MUTED)
        self._cx, self._cy, self._r = cx, cy, r

    # ---- 动画帧（30fps 由 DashboardApp._loop 驱动）-------------------
    def tick(self, status: str = "") -> None:
        if not self.alive:
            return
        self._frame += 1
        f = self._frame
        if self._closing:
            self._shrink_step()
            return
        cx, cy, r = self._cx, self._cy, self._r
        # 双弧反向旋转（科技扫描感）
        self.cv.itemconfig("arc1", start=(-f * 4) % 360)
        self.cv.itemconfig("arc2", start=(f * 3 + 180) % 360)
        # 呼吸光点绕外环公转
        ang = math.radians(-f * 4)
        pr = 3 + 1.6 * math.sin(f / 6.0)
        px, py = cx + r * math.cos(ang), cy + r * math.sin(ang)
        self.cv.coords("dot", px - pr, py - pr, px + pr, py + pr)
        # 状态文字 + 省略号动效
        dots = "·" * (1 + (f // 12) % 3)
        self.cv.itemconfig("status", text=f"{status or '正在唤醒弥悠'} {dots}")

    # ---- 收起 --------------------------------------------------------
    def request_close(self) -> None:
        """后端就绪/超时后调用；满足最短展示时长后进入缩圈收尾动画。"""
        if not self.alive or self._closing or self._close_req_frame is not None:
            return
        if self._frame < SPLASH_MIN_SEC * 30:
            self._close_req_frame = self._frame   # 记下请求，最短展示计满再关
            self.cv.after(int((SPLASH_MIN_SEC * 30 - self._frame) * 33),
                          self._begin_shrink)
        else:
            self._begin_shrink()

    def _begin_shrink(self) -> None:
        if self.alive and not self._closing:
            self._closing = True
            self._shrink_n = 6

    def _shrink_step(self) -> None:
        """6 帧缩圈：删静态层只留环收缩，然后自毁。"""
        self._shrink_n -= 1
        if self._shrink_n <= 0:
            self.destroy()
            return
        k = self._shrink_n / 6.0
        r = self._r * k
        cx, cy = self._cx, self._cy
        self.cv.delete("all")
        self.cv.create_oval(cx - r, cy - r, cx + r, cy + r,
                            outline=_RING1, width=3)

    def destroy(self) -> None:
        self.alive = False
        try:
            self.cv.destroy()
        except Exception:
            pass
