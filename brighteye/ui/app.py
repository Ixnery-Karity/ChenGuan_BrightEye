"""宸观 BrightEye 仪表盘（Tkinter · 双主题 · 零额外依赖）。

设计语言（v1.12.0 起随弥悠人设换代，配色 token 集中在 ui/theme.py）：
  · 🌙 弥悠·星夜（默认）：午夜紫深底 + 发光描边 + 紫/青/粉高饱和撞色；
  · 🍬 弥悠·奶糖：奶白粉紫浅底 + 糖果色卡片，可爱风；
  顶栏 🎨 按钮一键切换，选择持久化到 data/ui_theme.json。

与新系统的联动：
  - 顶部模式切换条（陪伴 / 严格 / 复盘 / 勿扰），点击即时切换；
  - 背景 ParticleField 随用眼状态变色提速（健康绿稳、告警红快）；
  - 悬浮桌宠「弥悠」常驻陪伴，气泡台词来自 persona；
  - 严格模式下不良习惯弹窗提醒，持续不改 → 颜色逐级加深。
"""

from __future__ import annotations

import tkinter as tk
from tkinter import font as tkfont

from ..config import CONFIG
from ..core.chat_engine import ChatEngine
from ..core.health_report import save_report
from ..core.modes import MODE_META, MODE_ORDER, Mode
from ..core.monitor import Monitor
from . import theme as theme_mod
from .chat import ChatWindow
from .particles import ParticleField
from .pet import FloatingPet

# 情绪 → (粒子 rgb, 速度)
_MOOD_FX = {
    "happy": ((46, 230, 166), 0.85),
    "normal": ((111, 231, 255), 1.0),
    "pout": ((255, 201, 77), 1.35),
    "angry": ((255, 82, 119), 1.95),
    "sleepy": ((138, 147, 181), 0.5),
}


class DashboardApp:
    def __init__(self, monitor: Monitor):
        self.m = monitor
        # —— 主题（双主题可切换，选择持久化）——
        self._data_dir = getattr(CONFIG, "data_dir", "data")
        self._theme_name = theme_mod.load_theme_name(self._data_dir)
        self.T = theme_mod.get_theme(self._theme_name)
        self.root = tk.Tk()
        self.root.title(f"{CONFIG.app_name} · {CONFIG.subtitle}")
        self.root.configure(bg=self.T["bg"])
        self.root.geometry("900x640")
        self.root.minsize(820, 600)

        self._f_logo = tkfont.Font(family="Microsoft YaHei", size=22, weight="bold")
        self._f_sub = tkfont.Font(family="Microsoft YaHei", size=10)
        self._f_metric = tkfont.Font(family="Consolas", size=34, weight="bold")
        self._f_unit = tkfont.Font(family="Microsoft YaHei", size=9)
        self._f_label = tkfont.Font(family="Microsoft YaHei", size=10)
        self._f_chip = tkfont.Font(family="Microsoft YaHei", size=10, weight="bold")
        self._f_body = tkfont.Font(family="Microsoft YaHei", size=10)

        self._mode_btns = {}
        self._strict_shown = {}     # category -> 已弹过的最高 severity
        self._popups = []           # 当前打开的严格弹窗
        self._frame = 0
        self._last_line = ""
        self._guard_win = None      # 当前极端用眼强制遮罩窗口(soft)
        # DDC/CI 物理亮度（建议4，可选依赖 monitorcontrol；默认关）
        from ..core.system_watch import BrightnessController
        self._brightness = (BrightnessController()
                            if CONFIG.system.brightness_enabled else None)

        self._build()
        self.chat_engine = ChatEngine(config=CONFIG)
        if getattr(CONFIG.llm, "warmup_enabled", True):
            self.chat_engine.warm_up_async()   # 后台预热聊天模型，首条对话不再冷启动
        self.chat_win = None
        self.pet = FloatingPet(
            self.root,
            on_open=self._open_dashboard,
            on_switch=self._switch_mode,
            on_quit=self._quit,
            on_chat=self._open_chat,
            mode_items=[(m.value, f"{MODE_META[m][1]} {MODE_META[m][0]}")
                        for m in MODE_ORDER],
        )
        self._sync_mode_ui()

        self._running = True
        self.root.protocol("WM_DELETE_WINDOW", self._hide_dashboard)
        self._loop()

    # ---- 布局 --------------------------------------------------------
    def _build(self) -> None:
        # 全窗粒子背景画布
        self.bg = tk.Canvas(self.root, bg=self.T["bg"], highlightthickness=0, bd=0)
        self.bg.place(x=0, y=0, relwidth=1, relheight=1)
        self.pf = ParticleField(self.bg, 900, 640,
                                count=CONFIG.ui_particle_count,
                                min_count=CONFIG.ui_particle_min)
        self.bg.bind("<Configure>", lambda e: self.pf.resize(e.width, e.height))

        # —— 顶部：LOGO + 数据源 ——
        head = tk.Frame(self.bg, bg=self.T["bg"])
        head.place(relx=0.03, y=18, relwidth=0.94)
        tk.Label(head, text=CONFIG.app_name, font=self._f_logo,
                 bg=self.T["bg"], fg=self.T["fg"]).pack(side="left")
        tk.Label(head, text="  " + CONFIG.subtitle, font=self._f_sub,
                 bg=self.T["bg"], fg=self.T["teal"]).pack(side="left", pady=(10, 0))
        theme_btn = tk.Label(
            head, text=f"🎨 {self.T['icon']} {self.T['label']}",
            font=self._f_sub, bg=self.T["panel"], fg=self.T["teal"],
            padx=10, pady=4, cursor="hand2")
        theme_btn.pack(side="right", pady=(6, 0))
        theme_btn.bind("<Button-1>", lambda e: self._toggle_theme())
        self.backend_lbl = tk.Label(head, text="", font=self._f_sub,
                                    bg=self.T["bg"], fg=self.T["muted"])
        self.backend_lbl.pack(side="right", padx=(0, 12), pady=(10, 0))

        # —— 模式切换条 ——
        modebar = tk.Frame(self.bg, bg=self.T["bg"])
        modebar.place(relx=0.03, y=64, relwidth=0.94)
        for m in MODE_ORDER:
            name, icon, _desc = MODE_META[m]
            b = tk.Label(modebar, text=f" {icon} {name} ", font=self._f_chip,
                         bg=self.T["panel"], fg=self.T["muted"], padx=10, pady=6, cursor="hand2")
            b.pack(side="left", padx=(0, 8))
            b.bind("<Button-1>", lambda e, k=m.value: self._switch_mode(k))
            self._mode_btns[m.value] = b
        self.mode_desc = tk.Label(modebar, text="", font=self._f_sub,
                                  bg=self.T["bg"], fg=self.T["muted"])
        self.mode_desc.pack(side="left", padx=(6, 0), pady=(4, 0))

        # —— HUD 指标卡 ——
        self.cards = {}
        specs = [
            ("blink", "眨眼频率", "次/分"),
            ("distance", "用眼距离", "cm"),
            ("cva", "颅椎角", "°"),
            ("screen", "屏幕时长", "min"),
        ]
        for i, (key, label, unit) in enumerate(specs):
            holder = tk.Frame(self.bg, bg=self.T["teal"], padx=2, pady=2)  # 外框=发光描边
            holder.place(relx=0.03 + i * 0.2425, y=110,
                         relwidth=0.225, height=128)
            card = tk.Frame(holder, bg=self.T["panel"])
            card.pack(fill="both", expand=True)
            val = tk.Label(card, text="--", font=self._f_metric, bg=self.T["panel"], fg=self.T["fg"])
            val.pack(anchor="w", padx=14, pady=(14, 0))
            sub = tk.Frame(card, bg=self.T["panel"])
            sub.pack(anchor="w", padx=14)
            tk.Label(sub, text=label, font=self._f_label,
                     bg=self.T["panel"], fg=self.T["muted"]).pack(side="left")
            tk.Label(sub, text=" " + unit, font=self._f_unit,
                     bg=self.T["panel"], fg=self.T["muted"]).pack(side="left")
            self.cards[key] = {"val": val, "glow": holder}

        # —— 弥悠台词条 ——
        strip = tk.Frame(self.bg, bg=self.T["panel2"], padx=14, pady=8)
        strip.place(relx=0.03, y=252, relwidth=0.94, height=44)
        self.mood_dot = tk.Label(strip, text="●", font=("Arial", 14),
                                 bg=self.T["panel2"], fg=self.T["teal"])
        self.mood_dot.pack(side="left", padx=(0, 8))
        tk.Label(strip, text="弥悠", font=self._f_chip,
                 bg=self.T["panel2"], fg=self.T["fg"]).pack(side="left", padx=(0, 10))
        self.persona_lbl = tk.Label(strip, text="哼，今天也勉强陪你一下好了。",
                                    font=self._f_body, bg=self.T["panel2"], fg=self.T["fg"],
                                    anchor="w")
        self.persona_lbl.pack(side="left", fill="x", expand=True)

        # —— 远眺倒计时 + 休息按钮 ——
        bar = tk.Frame(self.bg, bg=self.T["bg"])
        bar.place(relx=0.03, y=308, relwidth=0.94)
        self.break_lbl = tk.Label(bar, text="", font=self._f_body, bg=self.T["bg"], fg=self.T["teal"])
        self.break_lbl.pack(side="left")
        tk.Button(bar, text="我已远眺 / 休息", command=self.m.acknowledge_break,
                  bg=self.T["panel2"], fg=self.T["fg"], relief="flat", padx=16, pady=5,
                  activebackground=self.T["teal"], activeforeground=self.T["bg"],
                  font=self._f_chip).pack(side="right")

        # —— 实时建议 / 严格升级列表 ——
        advwrap = tk.Frame(self.bg, bg=self.T["bg"])
        advwrap.place(relx=0.03, y=348, relwidth=0.94, relheight=0.42)
        tk.Label(advwrap, text="实时健康建议", font=self._f_label,
                 bg=self.T["bg"], fg=self.T["muted"]).pack(anchor="w")
        self.adv_box = tk.Frame(advwrap, bg=self.T["panel"])
        self.adv_box.pack(fill="both", expand=True, pady=(6, 0))

        # —— 免责声明 ——
        tk.Label(self.bg, text=CONFIG.disclaimer, font=("Microsoft YaHei", 8),
                 bg=self.T["bg"], fg=self.T["muted"], wraplength=820, justify="left"
                 ).place(relx=0.03, rely=0.95, relwidth=0.94)

    # ---- 主题 --------------------------------------------------------
    def _level_color(self, level: str) -> str:
        return {"info": self.T["cyan"], "warn": self.T["amber"],
                "alert": self.T["coral"]}.get(level, self.T["cyan"])

    def _toggle_theme(self) -> None:
        """🎨 切换主题：换 token → 持久化 → 整体重建界面（widget 全在 self.bg 下）。"""
        self._theme_name = theme_mod.next_theme_name(self._theme_name)
        self.T = theme_mod.get_theme(self._theme_name)
        theme_mod.save_theme_name(self._theme_name, self._data_dir)
        self.root.configure(bg=self.T["bg"])
        self._mode_btns.clear()
        try:
            self.bg.destroy()
        except Exception:
            pass
        self._build()
        self._sync_mode_ui()

    # ---- 模式 --------------------------------------------------------
    def _switch_mode(self, mode_value: str) -> None:
        self.m.set_mode(mode_value)
        self._strict_shown.clear()
        self._sync_mode_ui()

    def _sync_mode_ui(self) -> None:
        cur = self.m.mode.value
        for val, btn in self._mode_btns.items():
            if val == cur:
                btn.config(bg=self.T["teal"], fg=self.T["bg"])
            else:
                btn.config(bg=self.T["panel"], fg=self.T["muted"])
        meta = MODE_META[self.m.mode]
        self.mode_desc.config(text="· " + meta[2])
        # 勿扰模式隐藏桌宠，其余常驻
        if hasattr(self, "pet"):
            if self.m.mode is Mode.SILENT:
                self.pet.hide()
            else:
                self.pet.show()

    # ---- 主循环（粒子 30fps，指标按 fps_target 节流）-----------------
    def _loop(self) -> None:
        if not self._running:
            return
        self._frame += 1
        self.pf.step()
        step = max(1, int(round(30 / max(1, CONFIG.fps_target))))
        if self._frame % step == 0:
            self._refresh()
        self.root.after(33, self._loop)

    def _refresh(self) -> None:
        snap = self.m.tick()
        backend_txt = f"数据源：{snap.backend}"
        if snap.game_mode:
            backend_txt += "   🎮 检测到全屏应用 · 已自动勿扰"
        self.backend_lbl.config(text=backend_txt)

        def fmt(v, nd=0):
            return f"{v:.{nd}f}" if v is not None else "--"

        t = CONFIG.thresholds
        br_color = self.T["teal"] if snap.blink_rate >= t.blink_rate_normal else (
            self.T["amber"] if snap.blink_rate >= t.blink_rate_low else self.T["coral"])
        self._set_card("blink", fmt(snap.blink_rate), br_color)
        d_color = self.T["teal"] if (snap.distance or 0) >= t.distance_min_cm else self.T["amber"]
        self._set_card("distance", fmt(snap.distance), d_color if snap.distance else self.T["muted"])
        cva_color = self.T["teal"] if (snap.cva or 0) >= t.cva_good else (
            self.T["amber"] if (snap.cva or 0) >= t.cva_warning else self.T["coral"])
        self._set_card("cva", fmt(snap.cva), cva_color if snap.cva else self.T["muted"])
        self._set_card("screen", fmt(snap.screen_time_min, 1), self.T["cyan"])

        # 远眺倒计时
        if not snap.face_present:
            self.break_lbl.config(text="未检测到人脸（已暂停计时）", fg=self.T["muted"])
        else:
            mm, ss = divmod(int(snap.next_break_in_sec), 60)
            self.break_lbl.config(
                text=f"距下次远眺 {mm:02d}:{ss:02d}   ·   连续用眼 "
                     f"{snap.continuous_use_min:.1f} 分钟", fg=self.T["teal"])

        # 弥悠情绪 / 台词
        rgb, spd = _MOOD_FX.get(snap.mood, _MOOD_FX["normal"])
        self.pf.set_mood(rgb, spd)
        self.mood_dot.config(fg="#%02x%02x%02x" % rgb)
        if snap.persona_line:
            self._last_line = snap.persona_line
            self.persona_lbl.config(text=snap.persona_line)
        self.pet.set_state(snap.mood, snap.persona_line)

        # 把当前用眼/情绪状态注入聊天引擎，让弥悠聊天时「看得懂你现在的样子」
        self.chat_engine.set_context(
            eye_context=self._eye_context(snap), emotion=snap.emotion)

        # 建议列表
        self._render_advices(snap)

        # 严格模式升级弹窗
        self._maybe_strict_popup(snap)

        # 极端用眼 → 强制干预（soft 遮罩 / hard 系统锁屏）
        if snap.guard_action is not None:
            self._trigger_guardian(snap.guard_action)

        # 复盘模式自动出报告提示
        if snap.auto_report_path:
            self.persona_lbl.config(
                text=f"复盘报告已生成：{snap.auto_report_path}")

    def _set_card(self, key, text, color) -> None:
        c = self.cards[key]
        c["val"].config(text=text, fg=color)
        c["glow"].config(bg=color)

    def _render_advices(self, snap) -> None:
        for w in self.adv_box.winfo_children():
            w.destroy()
        # 严格模式优先展示带升级严重度的提醒
        items = snap.strict_alerts if (snap.mode == "strict" and snap.strict_alerts) else None
        if items:
            for a in items:
                row = tk.Frame(self.adv_box, bg=self.T["panel"])
                row.pack(fill="x", padx=12, pady=5, anchor="w")
                tk.Label(row, text="▮" * (a.severity + 1), fg=a.color,
                         bg=self.T["panel"], font=("Arial", 11)).pack(side="left", padx=(0, 8))
                tk.Label(row, text=f"{a.title}（持续升级 Lv.{a.severity}）：{a.detail}",
                         font=self._f_body, bg=self.T["panel"], fg=self.T["fg"],
                         wraplength=720, justify="left").pack(side="left")
            return
        if snap.advices:
            for a in snap.advices:
                row = tk.Frame(self.adv_box, bg=self.T["panel"])
                row.pack(fill="x", padx=12, pady=5, anchor="w")
                tk.Label(row, text="●", fg=self._level_color(a.level.value), bg=self.T["panel"],
                         font=("Arial", 10)).pack(side="left", padx=(0, 8))
                tk.Label(row, text=f"{a.title}：{a.detail}", font=self._f_body,
                         bg=self.T["panel"], fg=self.T["fg"], wraplength=720, justify="left"
                         ).pack(side="left")
        else:
            tk.Label(self.adv_box, text="用眼习惯良好，继续保持 :)",
                     font=self._f_body, bg=self.T["panel"], fg=self.T["teal"]).pack(padx=12, pady=14)

    # ---- 严格模式弹窗（颜色逐级加深）---------------------------------
    def _maybe_strict_popup(self, snap) -> None:
        if snap.mode != "strict":
            self._strict_shown.clear()
            return
        active = set()
        for a in snap.strict_alerts:
            active.add(a.category)
            prev = self._strict_shown.get(a.category, -1)
            if a.severity > prev:   # 首次出现 or 升级 → 弹窗
                self._strict_shown[a.category] = a.severity
                self._show_popup(a)
        for cat in list(self._strict_shown):
            if cat not in active:
                self._strict_shown.pop(cat, None)

    def _show_popup(self, alert) -> None:
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes("-topmost", True)
        top.configure(bg=alert.color)
        body = tk.Frame(top, bg=self.T["panel"], padx=18, pady=14)
        body.pack(padx=3, pady=3)
        tk.Label(body, text=f"⚠ {alert.title}", font=self._f_chip,
                 bg=self.T["panel"], fg=alert.color).pack(anchor="w")
        tk.Label(body, text=f"严重度 Lv.{alert.severity} · 持续未改善",
                 font=("Microsoft YaHei", 8), bg=self.T["panel"], fg=self.T["muted"]
                 ).pack(anchor="w", pady=(2, 6))
        tk.Label(body, text=alert.detail, font=self._f_body, bg=self.T["panel"],
                 fg=self.T["fg"], wraplength=300, justify="left").pack(anchor="w")
        tk.Button(body, text="知道了", command=lambda: self._close_popup(top),
                  bg=alert.color, fg=self.T["bg"], relief="flat", padx=12, pady=3,
                  font=self._f_sub).pack(anchor="e", pady=(8, 0))

        sw = top.winfo_screenwidth()
        idx = len(self._popups)
        top.update_idletasks()
        w = top.winfo_reqwidth()
        top.geometry(f"+{sw - w - 30}+{60 + idx * 150}")
        self._popups.append(top)
        top.after(7000, lambda: self._close_popup(top))

    def _close_popup(self, top) -> None:
        if top in self._popups:
            self._popups.remove(top)
        try:
            top.destroy()
        except Exception:
            pass

    # ---- 情绪上下文 / 极端用眼强制干预 -------------------------------
    @staticmethod
    def _eye_context(snap) -> str:
        """把当前快照压成一句自然语言状态，喂给聊天大模型做上下文。"""
        parts = []
        if snap.blink_rate is not None:
            parts.append(f"眨眼{snap.blink_rate:.0f}次/分")
        if snap.distance is not None:
            parts.append(f"用眼距离{snap.distance:.0f}cm")
        if snap.cva is not None:
            parts.append(f"颅椎角{snap.cva:.0f}°")
        parts.append(f"已连续用眼{snap.continuous_use_min:.0f}分钟")
        return "、".join(parts)

    def _trigger_guardian(self, action) -> None:
        """执行强制干预：hard 真锁屏；soft 弹全屏遮罩+倒计时。"""
        if self._guard_win is not None:      # 已在干预中，避免叠加
            return
        if action.mode == "hard":
            from ..core.guardian import ScreenGuardian
            if ScreenGuardian.system_lock():
                return
            # 锁屏失败 → 降级 soft
        self._show_guard_overlay(action)

    def _show_guard_overlay(self, action) -> None:
        """soft 强制休息遮罩：全屏、置顶、倒计时结束前无法关闭。"""
        G = theme_mod.GUARD   # 遮罩固定暗色：休息时调暗环境，与主题无关
        win = tk.Toplevel(self.root)
        self._guard_win = win
        win.attributes("-topmost", True)
        try:
            win.attributes("-fullscreen", True)
        except Exception:
            win.geometry(f"{win.winfo_screenwidth()}x{win.winfo_screenheight()}+0+0")
        win.configure(bg=G["bg"])
        try:
            win.attributes("-alpha", 0.96)
        except Exception:
            pass
        win.protocol("WM_DELETE_WINDOW", lambda: None)  # 禁止直接关闭

        # 强制休息期间用 DDC/CI 调暗显示器物理亮度（可选，遮罩关闭时恢复）
        if self._brightness is not None:
            try:
                self._brightness.dim(CONFIG.system.rest_dim_percent)
            except Exception:
                pass

        wrap = tk.Frame(win, bg=G["bg"])
        wrap.place(relx=0.5, rely=0.5, anchor="center")
        tk.Label(wrap, text="⛔ 强制护眼休息", font=("Microsoft YaHei", 30, "bold"),
                 bg=G["bg"], fg=G["coral"]).pack(pady=(0, 10))
        tk.Label(wrap, text=action.line, font=("Microsoft YaHei", 15),
                 bg=G["bg"], fg=G["fg"], wraplength=680, justify="center").pack(pady=(0, 6))
        tk.Label(wrap, text=action.reason, font=("Microsoft YaHei", 10),
                 bg=G["bg"], fg=G["muted"], wraplength=680, justify="center").pack(pady=(0, 18))
        tk.Label(wrap, text="请闭眼放松、眺望 6 米外远处，让眼睛休息一下。",
                 font=("Microsoft YaHei", 12), bg=G["bg"], fg=G["teal"]).pack(pady=(0, 14))
        count_lbl = tk.Label(wrap, text="", font=("Consolas", 40, "bold"),
                             bg=G["bg"], fg=G["amber"])
        count_lbl.pack()
        btn = tk.Button(wrap, text="休息中…", state="disabled", relief="flat",
                        bg=G["panel2"], fg=G["muted"], font=self._f_chip, padx=20, pady=6)
        btn.pack(pady=(18, 0))

        def _countdown(remain: int) -> None:
            if self._guard_win is not win:
                return
            if remain <= 0:
                count_lbl.config(text="0")
                btn.config(text="我已休息，继续", state="normal", bg=G["teal"], fg=G["bg"],
                           cursor="hand2", command=_close, activebackground=G["cyan"])
                return
            count_lbl.config(text=str(remain))
            win.after(1000, lambda: _countdown(remain - 1))

        def _close() -> None:
            self._guard_win = None
            if self._brightness is not None:
                try:
                    self._brightness.restore()
                except Exception:
                    pass
            try:
                win.destroy()
            except Exception:
                pass
            self.m.acknowledge_break()   # 视为完成一次休息，重置计时

        _countdown(int(action.force_rest_sec))

    # ---- 窗口控制 ----------------------------------------------------
    def _open_dashboard(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(200, lambda: self.root.attributes("-topmost", False))

    def _open_chat(self) -> None:
        """右键「聊天模式」→ 打开/聚焦 galgame 风和弥悠聊天窗口（单例）。"""
        if self.chat_win is not None and self.chat_win.is_alive():
            self.chat_win.focus()
            return
        self.chat_win = ChatWindow(self.root, engine=self.chat_engine)

    def _hide_dashboard(self) -> None:
        """关闭仪表盘 → 收起为悬浮桌宠（不退出，弥悠继续陪伴）。"""
        self.root.withdraw()

    def _quit(self) -> None:
        self._running = False
        if self._brightness is not None:
            try:
                self._brightness.restore()  # 遮罩期间退出也要恢复亮度
            except Exception:
                pass
        try:
            path = save_report(self.m.metrics, CONFIG)
            print(f"\n[报告已保存] {path}")
        finally:
            try:
                self.pet.destroy()
            except Exception:
                pass
            self.m.close()
            self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()
