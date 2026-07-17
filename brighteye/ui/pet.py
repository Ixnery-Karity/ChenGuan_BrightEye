"""悬浮桌宠「文乃」——galgame 立绘风 · 担心程度差分动作 · 双轨渲染。

形象取自动漫《迷い猫オーバーラン!》芹泽文乃的官方设定：
  蜜金长发双马尾 + 双侧深红蝴蝶结 + 黑色十字发饰 · 翠绿大眼 ·
  白色短袖衬衫 + 黑色领带 · 酒红背带连衣裙(金色双排扣 + 白色裙摆滚边) ·
  黑色过膝袜 + 黑皮鞋。性格重度傲娇、言不由衷、猫系。

【渲染双轨】
  ① 外部立绘（galgame 风，推荐）：把 AI 生成的透明背景 PNG 立绘放进
     assets/pet/（按情绪命名，见该目录 README.txt），启动即自动加载，
     按「担心程度」选不同姿势差分立绘——这是 galgame 的标准做法（差分立绘）。
  ② 程序化矢量（兜底）：无 PNG 时用纯 Tkinter 矢量绘制 Q版文乃，
     并按情绪切换「不同身体动作」（满意举手 / 日常垂手 / 提醒食指点 +
     叉腰 / 生气双手叉腰 / 犯困揉眼），零外部素材，任何环境可演示。

Windows 下用 -transparentcolor 实现透明悬浮，非 Windows 自动降级深色小窗。
待机微动：呼吸浮动 + 双马尾/裙摆摆动 + 周期眨眼；脚下光环随情绪变色。
"""

from __future__ import annotations

import math
import os
import sys
import tkinter as tk
import tkinter.font as tkfont
from typing import Callable, Dict, Optional

from ..core.persona import (
    MOOD_COLOR, HAPPY, NORMAL, POUT, ANGRY, SLEEPY,
)

# 可选 Pillow：用于「立绘背景抠除」与「高质量(LANCZOS)缩放」。
# 缺 PIL 时自动退回 tk.PhotoImage（不抠图、整数倍缩放），demo 仍可运行。
try:
    from PIL import Image, ImageChops, ImageDraw, ImageTk
    _PIL_OK = True
except Exception:
    _PIL_OK = False

# 透明键色（不会出现在形象配色里的品红）
_KEY = "#ff00fe"

# —— 形象固定配色（贴合官方设定）——
_SKIN = "#ffe7d4"
_SKIN_SH = "#f1c7ab"
_HAIR = "#d8ad6a"        # 蜜金发
_HAIR_SH = "#b88f4a"
_HAIR_HI = "#efd49a"     # 发丝高光
_RIBBON = "#c81f33"      # 深红蝴蝶结
_RIBBON_SH = "#9c1526"
_SKIRT = "#b51d2e"       # 酒红背带裙
_SKIRT_SH = "#8c1322"
_TRIM = "#ffffff"        # 裙摆白色滚边
_BLOUSE = "#fbfdff"      # 白衬衫
_BLOUSE_SH = "#d6e0ef"
_TIE = "#1b2233"         # 黑色领带
_STOCK = "#1d2233"       # 黑色过膝袜
_STOCK_HI = "#2c3349"
_SHOE = "#121219"
_GOLD = "#e8b54a"        # 金色纽扣
_GOLD_SH = "#b6862a"
_EYE = "#2ee6a6"         # 翠绿瞳
_EYE_DK = "#0f9c72"
_WHITE = "#ffffff"
_INK = "#2a2138"
_MOUTH = "#c8556a"
_BLUSH = "#ff9aa6"
_CROSS = "#15131c"       # 黑色十字发饰


# —— 路线 B：外部 galgame 立绘（PNG）自动加载 ——
# 每个「担心程度」对应一张姿势差分立绘；缺图则回退矢量绘制。详见 README.txt。
ASSET_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "assets", "pet"))
SPRITE_FILES = {
    HAPPY: "wenna_happy.png",    # 用眼健康 → 满意
    NORMAL: "wenna_idle.png",    # 日常（基准立绘，必需）
    POUT: "wenna_pout.png",      # 轻度担心 → 提醒/嘟嘴
    ANGRY: "wenna_angry.png",    # 严重担心 → 生气/警告
    SLEEPY: "wenna_sleepy.png",  # 离座/犯困
}
BLINK_FILE = "wenna_blink.png"  # 通用眨眼（NORMAL/兜底）
# 每种担心程度各自的「闭眼差分」：有则眨眼时保持该情绪的姿势，避免动作突兀；
# 缺某一情绪的闭眼图时，自动回退到通用 wenna_blink.png（再无则保持睁眼）。
BLINK_FILES = {
    NORMAL: "wenna_blink.png",
    HAPPY: "wenna_happy_blink.png",
    POUT: "wenna_pout_blink.png",
    ANGRY: "wenna_angry_blink.png",
}
ART_TARGET_H = 300  # 立绘按整数倍缩放到的目标显示高度(像素)


def _lerp_hex(c0, c1, t):
    a = tuple(int(c0[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    return "#%02x%02x%02x" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _scale_to(img: "tk.PhotoImage", target_h: int) -> "tk.PhotoImage":
    """整数倍缩放 PhotoImage 至接近 target_h（无 PIL 时的兜底，保持边缘清晰）。"""
    h = img.height()
    if h <= 0:
        return img
    if h >= target_h:
        f = max(1, round(h / target_h))
        return img.subsample(f) if f > 1 else img
    f = max(1, round(target_h / h))
    return img.zoom(f) if f > 1 else img


def _pil_remove_bg(im, thresh: int = 60):
    """抠除立绘背景：
      - 若图片自带透明像素（alpha 已有 0），认为美术已抠好，原样返回；
      - 否则从四角泛洪填充(floodfill)判定连通的纯色背景，将其 alpha 置 0，
        前景（文乃本体）保持不透明。修掉「PNG 背景方框很突兀」的问题。
    thresh 越大容忍背景渐变越强，但过大可能误吃前景边缘。"""
    im = im.convert("RGBA")
    if im.getchannel("A").getextrema()[0] < 250:
        return im
    rgb = im.convert("RGB")
    w, h = rgb.size
    work = rgb.copy()
    sentinel = (255, 0, 254)
    for xy in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
        try:
            ImageDraw.floodfill(work, xy, sentinel, thresh=thresh)
        except Exception:
            pass
    # 被泛洪改写过的像素即背景 → diff>0；前景 diff==0
    diff = ImageChops.difference(work, rgb).convert("L")
    mask = diff.point(lambda v: 0 if v > 0 else 255)
    out = rgb.convert("RGBA")
    out.putalpha(mask)
    return out


def _pil_to_photo(im, target_h: int):
    """按目标高度等比 LANCZOS 缩放 PIL 图 → ImageTk.PhotoImage（高质量、不裁切）。"""
    w, h = im.size
    if h <= 0:
        return ImageTk.PhotoImage(im)
    th = max(1, int(target_h))
    tw = max(1, round(w * th / h))
    return ImageTk.PhotoImage(im.resize((tw, th), Image.LANCZOS))


def _load_sprites():
    """加载 assets/pet/ 下的立绘。
    优先 PIL：抠背景 + 显示时 LANCZOS 缩放（不裁切、清晰）；
    无 PIL 时退回 tk.PhotoImage（整数倍缩放）。
    返回 (sprites, pil_mode) 或 None（无基准 idle 图→回退矢量）。
    sprites 键：NORMAL/HAPPY/POUT/ANGRY/SLEEPY 与 'blink'。"""
    paths: Dict[str, str] = {}
    for mood, fname in SPRITE_FILES.items():
        p = os.path.join(ASSET_DIR, fname)
        if os.path.isfile(p):
            paths[mood] = p
    # 各情绪的闭眼差分（键 'blink_<mood>'）；通用眨眼另存 'blink'。
    for mood, fname in BLINK_FILES.items():
        p = os.path.join(ASSET_DIR, fname)
        if os.path.isfile(p):
            paths["blink_" + mood] = p
    if "blink_" + NORMAL in paths:
        paths["blink"] = paths["blink_" + NORMAL]
    if NORMAL not in paths:
        return None

    if _PIL_OK:
        try:
            out = {k: _pil_remove_bg(Image.open(p)) for k, p in paths.items()}
            return out, True
        except Exception:
            pass
    try:
        out = {k: tk.PhotoImage(file=p) for k, p in paths.items()}
        return out, False
    except Exception:
        return None


class FloatingPet:
    def __init__(
        self,
        root: tk.Tk,
        on_open: Optional[Callable[[], None]] = None,
        on_switch: Optional[Callable[[str], None]] = None,
        on_quit: Optional[Callable[[], None]] = None,
        on_chat: Optional[Callable[[], None]] = None,
        mode_items=None,
    ):
        self.on_open = on_open
        self.on_switch = on_switch
        self.on_quit = on_quit
        self.on_chat = on_chat
        self.mode_items = mode_items or []

        self.win = tk.Toplevel(root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self._transparent = False
        try:
            if sys.platform.startswith("win"):
                self.win.config(bg=_KEY)
                self.win.attributes("-transparentcolor", _KEY)
                self._transparent = True
        except Exception:
            self._transparent = False

        # —— 形象缩放（可拖拽移动 + 滚轮/右键菜单缩放）——
        self._scale = 1.0
        self._scale_min, self._scale_max = 0.6, 2.2
        self._disp_cache: Dict[str, object] = {}  # 当前缩放下的立绘缓存
        self._disp_cache_scale: Optional[float] = None

        # 优先加载外部 galgame 立绘，缺图回退矢量绘制（需在算窗口尺寸前）
        loaded = _load_sprites()
        if loaded:
            self._sprites, self._pil_mode = loaded
        else:
            self._sprites, self._pil_mode = None, False

        # 立绘留白：顶部给气泡预留空间（不再遮挡形象）；侧/底少量留白避免裁切。
        self._art_h = ART_TARGET_H
        self._pad_top, self._pad_side, self._pad_bot = 92, 24, 14
        if self._sprites:
            base = self._sprites[NORMAL]
            iw, ih = base.size if self._pil_mode else (base.width(), base.height())
            art_w0 = max(1, round(self._art_h * iw / max(1, ih)))
            self.W = art_w0 + 2 * self._pad_side
            self.H = self._pad_top + self._art_h + self._pad_bot
        else:
            self.W, self.H = 300, 380

        bg = _KEY if self._transparent else "#0a0e1a"
        self.canvas = tk.Canvas(self.win, width=self.W, height=self.H,
                                bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack()

        # 初始放到屏幕右下角
        sw = self.win.winfo_screenwidth()
        sh = self.win.winfo_screenheight()
        self.win.geometry(f"{self.W}x{self.H}+{sw - self.W - 30}+{sh - self.H - 60}")

        # 状态
        self._mood = NORMAL
        self._mood_col = MOOD_COLOR[NORMAL]
        self._target_col = MOOD_COLOR[NORMAL]
        self._t = 0.0
        self._blink = 0.0           # 0 睁眼 1 闭眼
        self._next_blink = 90       # 距下次眨眼的帧数（越大越不频繁）
        self._fonts: Dict[int, tkfont.Font] = {}  # 气泡字体缓存（按字号）
        self._bubble = ""
        self._bubble_ttl = 0
        self._running = True

        # 拖拽 / 点击
        self._press = None
        self._moved = False
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_menu)
        self.canvas.bind("<MouseWheel>", self._on_wheel)        # Windows/Mac 滚轮缩放
        self.canvas.bind("<Button-4>", self._on_wheel)          # Linux 上滚
        self.canvas.bind("<Button-5>", self._on_wheel)          # Linux 下滚
        self._menu = tk.Menu(self.win, tearoff=0)

        self._cx = self.W // 2       # 形象水平中心
        self._hy0 = 96               # 头部中心基准 y（再叠加呼吸浮动）
        self._tick()

    # ---- 对外接口 ----
    def set_state(self, mood: str, line: Optional[str] = None) -> None:
        self._mood = mood
        self._target_col = MOOD_COLOR.get(mood, MOOD_COLOR[NORMAL])
        if line:
            self.say(line)

    def say(self, text: str, ttl: int = 90) -> None:
        self._bubble = text
        self._bubble_ttl = ttl

    def show(self) -> None:
        self.win.deiconify()

    def hide(self) -> None:
        self.win.withdraw()

    def destroy(self) -> None:
        self._running = False
        try:
            self.win.destroy()
        except Exception:
            pass

    # ---- 交互 ----
    def _on_press(self, e):
        self._press = (e.x_root, e.y_root, self.win.winfo_x(), self.win.winfo_y())
        self._moved = False

    def _on_drag(self, e):
        if not self._press:
            return
        dx = e.x_root - self._press[0]
        dy = e.y_root - self._press[1]
        if abs(dx) + abs(dy) > 4:
            self._moved = True
        self.win.geometry(f"+{self._press[2] + dx}+{self._press[3] + dy}")

    def _on_release(self, e):
        if not self._moved and self.on_open:
            self.on_open()
        self._press = None

    def _on_menu(self, e):
        m = self._menu
        m.delete(0, "end")
        for key, label in self.mode_items:
            m.add_command(label=label,
                          command=lambda k=key: self.on_switch and self.on_switch(k))
        m.add_separator()
        # 形象大小：右键选择（也可用鼠标滚轮无级缩放）
        size_menu = tk.Menu(m, tearoff=0)
        for label, sc in (("小  75%", 0.75), ("标准 100%", 1.0),
                          ("大  140%", 1.4), ("超大 180%", 1.8)):
            mark = " ✓" if abs(self._scale - sc) < 1e-3 else ""
            size_menu.add_command(label=label + mark,
                                  command=lambda v=sc: self._apply_scale(v))
        size_menu.add_separator()
        size_menu.add_command(label="放大 +", command=lambda: self._apply_scale(self._scale + 0.15))
        size_menu.add_command(label="缩小 −", command=lambda: self._apply_scale(self._scale - 0.15))
        m.add_cascade(label="形象大小（滚轮可缩放）", menu=size_menu)
        m.add_separator()
        m.add_command(label="💬 聊天模式（和文乃说说话）",
                      command=lambda: self.on_chat and self.on_chat())
        m.add_command(label="打开仪表盘", command=lambda: self.on_open and self.on_open())
        m.add_command(label="退出", command=lambda: self.on_quit and self.on_quit())
        try:
            m.tk_popup(e.x_root, e.y_root)
        finally:
            m.grab_release()

    def _on_wheel(self, e):
        # Windows: e.delta ±120；Linux: Button-4 上滚/Button-5 下滚
        up = getattr(e, "delta", 0) > 0 or getattr(e, "num", 0) == 4
        self._apply_scale(self._scale + (0.1 if up else -0.1))

    def _apply_scale(self, s: float) -> None:
        """设置形象缩放：限幅 → 重设画布/窗口尺寸(保持左上角) → 失效立绘缓存。"""
        s = max(self._scale_min, min(self._scale_max, round(s, 3)))
        if abs(s - self._scale) < 1e-3:
            return
        self._scale = s
        self._disp_cache.clear()
        self._disp_cache_scale = None
        cw, ch = int(self.W * s), int(self.H * s)
        x, y = self.win.winfo_x(), self.win.winfo_y()
        self.canvas.config(width=cw, height=ch)
        self.win.geometry(f"{cw}x{ch}+{x}+{y}")

    # ---- 动画主循环 ----
    def _tick(self):
        if not self._running:
            return
        self._t += 1
        self._mood_col = _lerp_hex(self._mood_col, self._target_col, 0.12)

        # 眨眼节律（频率调低：约每 4~9 秒眨一次，闭眼约 0.2s）
        self._next_blink -= 1
        if self._next_blink <= 0:
            self._blink = 1.0
            if self._next_blink < -6:
                self._blink = 0.0
                self._next_blink = 120 + int(150 * abs(math.sin(self._t * 1.3)))
        if self._bubble_ttl > 0:
            self._bubble_ttl -= 1

        self._draw()
        self.win.after(33, self._tick)

    # ---- 当前缩放下的立绘（缓存，避免每帧重算缩放）----
    def _disp_sprite(self, key: str):
        if self._disp_cache_scale != self._scale:
            self._disp_cache.clear()
            self._disp_cache_scale = self._scale
        img = self._disp_cache.get(key)
        if img is None:
            base = self._sprites.get(key) or self._sprites[NORMAL]
            th = int(self._art_h * self._scale)
            img = _pil_to_photo(base, th) if self._pil_mode else _scale_to(base, th)
            self._disp_cache[key] = img
        return img

    # ---- 绘制（先判双轨，再分层）----
    def _draw(self):
        c = self.canvas
        c.delete("all")
        sc = self._scale
        t = self._t
        col = self._mood_col
        sleepy = self._mood == SLEEPY

        # —— 路线 B：外部 galgame 立绘 —— 按担心程度选姿势差分（按 sc 缩放）
        if self._sprites:
            self._draw_sprite(c, int(self.W * sc) // 2, t, col, sleepy, sc)
            if self._bubble and self._bubble_ttl > 0:
                self._draw_bubble(self._bubble, col, sc)
            return

        # —— 兜底：程序化矢量 文乃（先按基准坐标绘制，末尾整体缩放 sc）——
        cx = self._cx
        bob = math.sin(t * 0.12) * 4.0
        hy = self._hy0 + bob               # 头部中心 y
        sway = math.sin(t * 0.10) * 5.0    # 双马尾/裙摆摆动

        angry = self._mood == ANGRY
        pout = self._mood == POUT
        happy = self._mood == HAPPY

        # 1) 脚下光环（情绪变色 · HUD 感）
        gy = hy + 252
        c.create_oval(cx - 78, gy - 12, cx + 78, gy + 12,
                      fill=_lerp_hex(col, "#0a0e1a", 0.72), outline="")
        c.create_oval(cx - 70, gy - 10, cx + 70, gy + 10, outline=col, width=2)
        c.create_oval(cx - 50, gy - 7, cx + 50, gy + 7,
                      outline=_lerp_hex(col, "#0a0e1a", 0.4), width=1)

        # 2) 后发 + 双马尾（最底层）
        c.create_oval(cx - 54, hy - 50, cx + 54, hy + 60, fill=_HAIR, outline=_HAIR_SH)
        c.create_polygon(
            cx - 44, hy + 14, cx - 54, hy + 90, cx - 40, hy + 168,
            cx, hy + 188, cx + 40, hy + 168, cx + 54, hy + 90, cx + 44, hy + 14,
            fill=_HAIR, outline=_HAIR_SH, smooth=True)
        for sgn in (-1, 1):
            bx = cx + sgn * 44
            c.create_line(
                bx, hy - 40, bx + sgn * 16, hy + 40,
                bx + sgn * 10 + sway * sgn, hy + 150,
                bx + sgn * 2 + sway * sgn, hy + 196,
                smooth=True, width=24, fill=_HAIR, capstyle="round")
            c.create_line(
                bx, hy - 30, bx + sgn * 12 + sway * sgn, hy + 120,
                bx + sgn * 2 + sway * sgn, hy + 188,
                smooth=True, width=6, fill=_HAIR_HI, capstyle="round")

        # 3) 腿（过膝袜）+ 鞋
        ly = hy + 150
        for sgn in (-1, 1):
            lx = cx + sgn * 16
            c.create_line(lx, ly, lx + sgn * 3, ly + 78, width=17,
                          fill=_STOCK, capstyle="round")
            c.create_line(lx - 4, ly + 6, lx - 4, ly + 64, width=3, fill=_STOCK_HI)
            c.create_line(lx - 8, ly + 2, lx + 8, ly + 2, width=3, fill=_STOCK_HI)
            fx = lx + sgn * 3
            c.create_oval(fx - 12, ly + 74, fx + 14, ly + 92, fill=_SHOE, outline="")

        # 4) 酒红背带连衣裙
        sk_top = hy + 96
        sk_bot = hy + 152
        c.create_polygon(
            cx - 22, sk_top, cx + 22, sk_top,
            cx + 48 + sway * 0.4, sk_bot, cx - 48 + sway * 0.4, sk_bot,
            fill=_SKIRT, outline=_SKIRT_SH, smooth=False)
        c.create_line(cx - 48 + sway * 0.4, sk_bot, cx + 48 + sway * 0.4, sk_bot,
                      width=5, fill=_TRIM, capstyle="round")
        for col_x in (cx - 12, cx + 12):
            for row_y in (sk_top + 12, sk_top + 30):
                c.create_oval(col_x - 3, row_y - 3, col_x + 3, row_y + 3,
                              fill=_GOLD, outline=_GOLD_SH)

        # 5) 白衬衫上身（手臂改由 _draw_arms 按情绪绘制不同动作）
        bl_top = hy + 54
        c.create_polygon(
            cx - 26, bl_top, cx + 26, bl_top,
            cx + 24, sk_top + 4, cx - 24, sk_top + 4,
            fill=_BLOUSE, outline=_BLOUSE_SH, smooth=False)
        for sgn in (-1, 1):              # 背带（酒红）
            sx = cx + sgn * 14
            c.create_line(sx, bl_top + 2, sx, sk_top + 2, width=6, fill=_SKIRT)
        self._draw_arms(c, cx, bl_top, sk_top, hy, self._mood, sway)

        # 6) 领口 + 黑色领带
        c.create_polygon(cx - 12, bl_top, cx, bl_top + 12, cx + 12, bl_top,
                         fill=_BLOUSE, outline=_BLOUSE_SH)
        c.create_polygon(cx, bl_top + 4, cx + 6, bl_top + 12, cx, bl_top + 16,
                         cx - 6, bl_top + 12, fill=_TIE, outline="")
        c.create_polygon(cx - 5, bl_top + 14, cx + 5, bl_top + 14,
                         cx + 7, bl_top + 44, cx, bl_top + 50, cx - 7, bl_top + 44,
                         fill=_TIE, outline="")

        # 7) 头（皮肤）
        c.create_oval(cx - 44, hy - 46, cx + 44, hy + 50, fill=_SKIN, outline=_SKIN_SH)

        # 8) 刘海
        c.create_arc(cx - 48, hy - 54, cx + 48, hy + 24, start=8, extent=164,
                     fill=_HAIR, outline=_HAIR_SH, style="pieslice")
        c.create_polygon(cx - 12, hy - 20, cx + 12, hy - 20, cx, hy + 10,
                         fill=_HAIR, outline=_HAIR_SH)
        for sgn in (-1, 1):
            c.create_polygon(cx + sgn * 26, hy - 28, cx + sgn * 40, hy - 24,
                             cx + sgn * 30, hy + 6, fill=_HAIR, outline=_HAIR_SH)
        c.create_line(cx - 18, hy - 36, cx - 6, hy - 8, width=2, fill=_HAIR_HI)

        # 9) 表情
        self._draw_face(c, cx, hy, sleepy, angry, pout, happy)

        # 10) 前侧发束（框脸）
        for sgn in (-1, 1):
            ox = cx + sgn * 40
            c.create_line(ox, hy - 30, ox + sgn * 6, hy + 6,
                          ox - sgn * 2, hy + 40, smooth=True, width=13,
                          fill=_HAIR, capstyle="round")

        # 11) 双侧红蝴蝶结 + 黑色十字发饰
        self._draw_bow(c, cx - 42, hy - 40, sway)
        self._draw_bow(c, cx + 42, hy - 40, sway)
        crx, cry = cx - 50, hy - 18
        c.create_rectangle(crx - 2, cry - 7, crx + 2, cry + 7, fill=_CROSS, outline="")
        c.create_rectangle(crx - 6, cry - 2, crx + 6, cry + 2, fill=_CROSS, outline="")

        # 12) 睡眠 Zzz（字号随缩放，几何位置交给末尾整体缩放）
        if sleepy:
            for i, fs in enumerate((10, 14, 18)):
                c.create_text(cx + 44 + i * 12, hy - 44 - i * 16, text="z",
                              fill="#aeb8d8", font=("Arial", max(6, int(fs * sc)), "bold"))

        # —— 整体缩放：几何 + 线宽一并放大/缩小，保持矢量清晰 ——
        if abs(sc - 1.0) > 1e-3:
            c.scale("all", 0, 0, sc, sc)
            for it in c.find_all():
                try:
                    wdt = float(c.itemcget(it, "width"))
                except Exception:
                    continue
                if wdt:
                    c.itemconfigure(it, width=max(1.0, wdt * sc))

        # 13) 气泡台词（固定大小便于阅读，置于缩放之外）
        if self._bubble and self._bubble_ttl > 0:
            self._draw_bubble(self._bubble, col, sc)

    def _blink_key(self, mood: str) -> Optional[str]:
        """挑选当前情绪的闭眼帧：优先该情绪自己的闭眼差分；
        没有则回退到通用眨眼('blink')；再没有则返回 None（保持睁眼，不做突兀切换）。"""
        k = "blink_" + mood
        if k in self._sprites:
            return k
        if "blink" in self._sprites:
            return "blink"
        return None

    # ---- 外部立绘渲染（按担心程度选差分，按 sc 缩放）----
    def _draw_sprite(self, c, cx, t, col, sleepy, sc):
        # 睡眠时保持闭眼，不再插入眨眼帧（修「睡眠还会眨眼」）。
        key = self._mood
        if self._blink > 0.5 and not sleepy:
            bk = self._blink_key(self._mood)   # 该情绪闭眼差分 → 通用眨眼 → 保持睁眼
            if bk:
                key = bk
        img = self._disp_sprite(key)
        bob = math.sin(t * 0.12) * 3.0 * sc
        # 顶部预留气泡区后再放立绘；不再画脚下光环（修「光环突兀」）。
        top = self._pad_top * sc + bob
        c.create_image(cx, top, anchor="n", image=img)
        if sleepy:
            for i, fs in enumerate((10, 14, 18)):
                c.create_text(cx + 52 * sc + i * 12 * sc, top + 12 * sc - i * 16 * sc,
                              text="z", fill="#aeb8d8",
                              font=("Arial", max(6, int(fs * sc)), "bold"))

    # ---- 手臂动作（按担心程度差分：矢量兜底用）----
    def _draw_arms(self, c, cx, bl_top, sk_top, hy, mood, sway):
        happy = mood == HAPPY
        angry = mood == ANGRY
        pout = mood == POUT
        sleepy = mood == SLEEPY
        for sgn in (-1, 1):
            sh_x = cx + sgn * 30                      # 肩/泡泡袖中心
            c.create_oval(sh_x - 13, bl_top + 2, sh_x + 13, bl_top + 26,
                          fill=_BLOUSE, outline=_BLOUSE_SH)   # 泡泡短袖
            # 依「担心程度」决定手与肘位置 → 不同身体动作
            finger = False
            if angry or (pout and sgn < 0):
                hx, hyy = cx + sgn * 22, sk_top + 8           # 叉腰
                elx, ely = sh_x + sgn * 12, bl_top + 26
            elif happy:
                hx, hyy = sh_x + sgn * 12, bl_top - 16        # 双手举起欢呼
                elx, ely = sh_x + sgn * 8, bl_top + 8
            elif pout and sgn > 0:
                hx, hyy = sh_x + sgn * 4, bl_top - 18         # 右手食指上举提醒
                elx, ely = sh_x + sgn * 6, bl_top + 6
                finger = True
            elif sleepy and sgn > 0:
                hx, hyy = cx + sgn * 20, hy + 8               # 右手抬到脸侧揉眼
                elx, ely = sh_x, bl_top + 12
            else:
                hx, hyy = sh_x + sgn * 4, bl_top + 40         # 自然垂手
                elx, ely = sh_x + sgn * 2, bl_top + 22
            c.create_line(sh_x, bl_top + 22, elx, ely, hx, hyy,
                          smooth=True, width=9, fill=_SKIN, capstyle="round")  # 小臂
            c.create_oval(hx - 7, hyy - 7, hx + 7, hyy + 7,
                          fill=_SKIN, outline=_SKIN_SH)        # 小手
            if finger:
                c.create_line(hx, hyy - 5, hx, hyy - 15, width=4,
                              fill=_SKIN, capstyle="round")    # 食指

    def _draw_face(self, c, cx, hy, sleepy, angry, pout, happy):
        eye_y = hy + 6
        edx = 19
        closed = self._blink > 0.5 or sleepy
        for sgn in (-1, 1):
            ex = cx + sgn * edx
            if closed:
                c.create_arc(ex - 11, eye_y - 8, ex + 11, eye_y + 10,
                             start=20, extent=140, style="arc", width=3, outline=_INK)
            else:
                c.create_oval(ex - 11, eye_y - 14, ex + 11, eye_y + 14,
                              fill=_WHITE, outline=_INK, width=2)        # 眼白
                c.create_oval(ex - 9, eye_y - 12, ex + 9, eye_y + 12,
                              fill=_EYE, outline=_EYE_DK)                # 翠绿虹膜
                c.create_oval(ex - 8, eye_y, ex + 8, eye_y + 12,
                              fill=_EYE_DK, outline="")                  # 下半渐深
                c.create_oval(ex - 4, eye_y - 5, ex + 4, eye_y + 7,
                              fill=_INK, outline="")                     # 瞳孔
                c.create_oval(ex - 5, eye_y - 9, ex - 1, eye_y - 4,
                              fill=_WHITE, outline="")                   # 大高光
                c.create_oval(ex + 2, eye_y + 3, ex + 5, eye_y + 6,
                              fill=_WHITE, outline="")                   # 小高光
            if angry:
                c.create_line(ex - 11, eye_y - 19, ex + 8, eye_y - 13,
                              width=3, fill=_RIBBON_SH, capstyle="round")

        if not sleepy:
            bw = 11 if pout else 8
            for sgn in (-1, 1):
                bxx = cx + sgn * 31
                c.create_oval(bxx - bw, eye_y + 13, bxx + bw, eye_y + 24,
                              fill=_BLUSH, outline="")

        my = eye_y + 30
        if angry:
            c.create_oval(cx - 7, my - 4, cx + 7, my + 10, fill=_MOUTH, outline="")
        elif happy:
            c.create_arc(cx - 11, my - 12, cx + 11, my + 8, start=200, extent=140,
                         style="arc", width=3, outline=_MOUTH)
        elif pout:
            c.create_arc(cx - 9, my - 2, cx + 9, my + 12, start=20, extent=140,
                         style="arc", width=3, outline=_MOUTH)
        elif sleepy:
            c.create_line(cx - 4, my, cx + 4, my, width=2, fill=_MOUTH)
        else:
            c.create_line(cx - 6, my, cx + 6, my, width=2, fill=_MOUTH, capstyle="round")
        if not sleepy and not angry:
            c.create_polygon(cx - 5, my, cx - 1, my, cx - 3, my + 6,
                             fill=_WHITE, outline=_SKIN_SH)

    def _draw_bow(self, c, x, y, sway):
        """一只深红蝴蝶结（两片环 + 结心 + 飘带）。"""
        for sgn in (-1, 1):
            c.create_polygon(
                x, y + 4, x + sgn * 6, y + 22 + sway * 0.3,
                x + sgn * 12, y + 18 + sway * 0.3,
                fill=_RIBBON_SH, outline="")
        c.create_polygon(x, y, x - 16, y - 9, x - 16, y + 9,
                         fill=_RIBBON, outline=_RIBBON_SH)
        c.create_polygon(x, y, x + 16, y - 9, x + 16, y + 9,
                         fill=_RIBBON, outline=_RIBBON_SH)
        c.create_oval(x - 4, y - 4, x + 4, y + 4, fill=_RIBBON_SH, outline="")

    # ---- 气泡辅助：字体缓存 + 按实际像素宽度换行（修「文字超出气泡」）----
    def _bubble_font(self, fs: int) -> "tkfont.Font":
        f = self._fonts.get(fs)
        if f is None:
            f = tkfont.Font(family="Microsoft YaHei", size=fs)
            self._fonts[fs] = f
        return f

    @staticmethod
    def _wrap_by_px(text: str, font: "tkfont.Font", max_px: int):
        """按真实文本像素宽逐字换行，保证每行不超过 max_px（中英文混排都适用）。"""
        lines, cur = [], ""
        for ch in text:
            if ch == "\n":
                lines.append(cur)
                cur = ""
                continue
            if font.measure(cur + ch) <= max_px:
                cur += ch
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
        return lines or [""]

    @staticmethod
    def _round_rect_pts(x0, y0, x1, y1, r):
        """生成圆角矩形多边形点序列（配 smooth=True 画出圆角，二次元可爱风）。"""
        return [
            x0 + r, y0,  x1 - r, y0,  x1, y0,  x1, y0 + r,
            x1, y1 - r,  x1, y1,  x1 - r, y1,  x0 + r, y1,
            x0, y1,  x0, y1 - r,  x0, y0 + r,  x0, y0,
        ]

    def _draw_bubble(self, text: str, col: str, sc: float = 1.0):
        """二次元可爱风气泡：奶白圆角卡 + 彩色描边 + 顶部小爱心 + 朝下圆尾。
        置于顶部预留区、水平居中、按真实像素宽换行——不超框、不挡脸。"""
        c = self.canvas
        cw = int(self.W * sc)
        fs = max(9, int(11 * sc))
        font = self._bubble_font(fs)

        pad_x = 14 * sc                     # 文字左右内边距
        max_box = min(cw - 16 * sc, 200 * sc)      # 气泡最大宽度
        max_text_px = int(max_box - 2 * pad_x)
        lines = self._wrap_by_px(text, font, max_text_px)[:4]

        line_h = font.metrics("linespace")
        text_w = max(font.measure(ln) for ln in lines)
        bw = text_w + 2 * pad_x
        bw = max(bw, 44 * sc)
        bh = len(lines) * line_h + 12 * sc

        bx = (cw - bw) / 2.0
        by = 8 * sc
        r = min(16 * sc, bh / 2, bw / 2)    # 圆角半径

        soft = "#FFF3FA"                    # 奶白偏粉的可爱底
        lw = max(1, int(2 * sc))
        # 阴影（轻微下移的同色淡影，增加“贴纸感”）
        c.create_polygon(self._round_rect_pts(bx + 2 * sc, by + 3 * sc,
                                              bx + bw + 2 * sc, by + bh + 3 * sc, r),
                         smooth=True, fill=_lerp_hex(col, "#0a0e1a", 0.78), outline="")
        # 朝下的圆尾（水平居中，指向文乃头顶）
        tx = cw / 2.0
        ty = by + bh
        c.create_polygon(tx - 9 * sc, ty - 2 * sc, tx + 9 * sc, ty - 2 * sc,
                         tx + 2 * sc, ty + 11 * sc, tx, ty + 13 * sc,
                         tx - 2 * sc, ty + 11 * sc,
                         smooth=True, fill=soft, outline=col, width=lw)
        # 主体圆角卡
        c.create_polygon(self._round_rect_pts(bx, by, bx + bw, by + bh, r),
                         smooth=True, fill=soft, outline=col, width=lw)
        # 顶部小爱心装饰（二次元点缀）
        hx, hy, hs = bx + bw - 10 * sc, by + 2 * sc, 3.0 * sc
        for dxs in (-1, 1):
            c.create_oval(hx + dxs * hs - hs, hy, hx + dxs * hs + hs, hy + 2 * hs,
                          fill=col, outline="")
        c.create_polygon(hx - 2 * hs, hy + hs, hx + 2 * hs, hy + hs, hx, hy + 3.4 * hs,
                         fill=col, outline="")
        # 文字（深墨色，居中）
        for i, ln in enumerate(lines):
            c.create_text(tx, by + 6 * sc + i * line_h, text=ln, anchor="n",
                          fill="#3A2B45", font=font)
