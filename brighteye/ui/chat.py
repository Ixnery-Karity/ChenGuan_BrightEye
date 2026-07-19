"""galgame 风「和弥悠聊天」窗口 —— 立绘 + 底部对话框 + 好感度条 + 自动对话。

交互（对标 galgame，但不给选项分支）：
  · 立绘居中偏右，左/下方是 galgame 经典对话框（半透明深色 + 名字牌 + 文字）。
  · 玩家在底部输入框自由打字 → 回车/发送，弥悠自动回数句（伪多条对话）；
    每句点「继续 ▼」或按空格推进，放完才等下一次输入。
  · 顶部好感度条：心数 + 等级名 + 进度条，随对话实时变化（像 galgame 恋爱进度）。
  · 长时间不说话，弥悠会主动开口（自动对话）。

立绘复用桌宠的 assets/pet 加载逻辑；缺图时用占位卡，演示照常可用。
"""

from __future__ import annotations

import threading
import tkinter as tk
import tkinter.font as tkfont
from typing import Optional

from ..core.chat_engine import ChatEngine, ChatTurn, AFFECTION_MAX
from ..core.persona import MOOD_COLOR, NORMAL
from .pet import _load_sprites, _pil_to_photo, _scale_to, ART_TARGET_H, NORMAL as _N

# galgame 配色
_BG = "#0A0E1A"
_BOX = "#10162B"          # 对话框底
_BOX_EDGE = "#2EE6A6"
_NAME_BG = "#2EE6A6"
_FG = "#EAF0FF"
_MUTED = "#8A93B5"
_HEART_ON = "#FF5277"
_HEART_OFF = "#33405E"
_INPUT_BG = "#161E36"

_TYPE_MS = 28            # 打字机每字间隔(ms)
_IDLE_MS = 18000         # 无输入多久后弥悠主动开口(ms)


class ChatWindow:
    """单例式聊天窗口；和弥悠 galgame 风自动对话。"""

    def __init__(self, root: tk.Tk, engine: Optional[ChatEngine] = None,
                 on_affection=None):
        self.engine = engine or ChatEngine()
        self.on_affection = on_affection      # 好感度变化回调(可选)
        self._art_h = 360

        self.win = tk.Toplevel(root)
        self.win.title("和弥悠聊天 · 宸观 BrightEye")
        self.win.configure(bg=_BG)
        self.win.geometry("680x520")
        self.win.minsize(560, 460)

        self._f_name = tkfont.Font(family="Microsoft YaHei", size=13, weight="bold")
        self._f_text = tkfont.Font(family="Microsoft YaHei", size=13)
        self._f_small = tkfont.Font(family="Microsoft YaHei", size=9)
        self._f_lvl = tkfont.Font(family="Microsoft YaHei", size=11, weight="bold")
        self._f_btn = tkfont.Font(family="Microsoft YaHei", size=10, weight="bold")

        # 立绘
        loaded = _load_sprites()
        if loaded:
            self._sprites, self._pil_mode = loaded
        else:
            self._sprites, self._pil_mode = None, False
        self._photo_cache = {}

        # 对话推进状态
        self._queue = []            # 待逐句显示的台词
        self._typing = ""           # 正在打字机输出的整句
        self._typed = 0             # 已输出字数
        self._type_job = None
        self._idle_job = None
        self._mood = NORMAL
        # LLM 异步：respond 在后台线程执行，UI 只显示「思考中」动画不卡死
        self._pending = False
        self._think_job = None
        self._think_dots = 0

        self._build()
        self._refresh_affection()
        self._push_turn(self.engine.greeting())

    # ---- 布局 ----
    def _build(self):
        cv = tk.Canvas(self.win, bg=_BG, highlightthickness=0, bd=0)
        cv.place(x=0, y=0, relwidth=1, relheight=1)
        self.cv = cv

        # 顶部好感度条
        top = tk.Frame(self.win, bg=_BG)
        top.place(relx=0.0, y=10, relwidth=1.0)
        self.heart_lbl = tk.Label(top, text="", font=self._f_lvl, bg=_BG, fg=_HEART_ON)
        self.heart_lbl.pack(side="left", padx=(18, 8))
        self.lvl_lbl = tk.Label(top, text="", font=self._f_small, bg=_BG, fg=_MUTED)
        self.lvl_lbl.pack(side="left")
        self.delta_lbl = tk.Label(top, text="", font=self._f_small, bg=_BG, fg=_HEART_ON)
        self.delta_lbl.pack(side="right", padx=(0, 18))
        # 进度条（画在画布上，_refresh_affection 时重绘）
        self._bar_y = 40

        # 立绘画布区域（中部）
        self.art = tk.Label(self.win, bg=_BG, bd=0)
        self.art.place(relx=0.62, y=54, anchor="n")

        # 底部 galgame 对话框
        box = tk.Frame(self.win, bg=_BOX_EDGE)
        box.place(relx=0.04, rely=0.62, relwidth=0.92, relheight=0.24)
        inner = tk.Frame(box, bg=_BOX)
        inner.pack(fill="both", expand=True, padx=2, pady=2)
        name_tag = tk.Label(inner, text="  弥悠  ", font=self._f_name,
                            bg=_NAME_BG, fg=_BG)
        name_tag.place(x=14, y=-2)
        self.text_lbl = tk.Label(inner, text="", font=self._f_text, bg=_BOX, fg=_FG,
                                 wraplength=520, justify="left", anchor="nw")
        self.text_lbl.place(x=18, y=30, relwidth=0.95, relheight=0.7)
        self.cont_lbl = tk.Label(inner, text="", font=self._f_small, bg=_BOX, fg=_BOX_EDGE)
        self.cont_lbl.place(relx=0.92, rely=0.74)
        inner.bind("<Button-1>", lambda e: self._advance())
        self.text_lbl.bind("<Button-1>", lambda e: self._advance())

        # 底部输入区
        bottom = tk.Frame(self.win, bg=_BG)
        bottom.place(relx=0.04, rely=0.89, relwidth=0.92)
        self.entry = tk.Entry(bottom, font=self._f_text, bg=_INPUT_BG, fg=_FG,
                              insertbackground=_FG, relief="flat")
        self.entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self.entry.bind("<Return>", lambda e: self._send())
        tk.Button(bottom, text="发送", font=self._f_btn, bg=_BOX_EDGE, fg=_BG,
                  relief="flat", padx=18, command=self._send,
                  activebackground=_HEART_ON, activeforeground=_FG).pack(side="right")

        self.win.bind("<space>", lambda e: self._advance() if not self._entry_focused() else None)
        self.win.after(100, lambda: self.entry.focus_set())

    def _entry_focused(self) -> bool:
        try:
            return self.win.focus_get() is self.entry
        except Exception:
            return False

    # ---- 立绘 ----
    def _set_art(self, mood: str):
        self._mood = mood
        if not self._sprites:
            # 无立绘：用情绪色块占位
            self.art.config(image="", text="弥悠", font=self._f_name,
                            fg=MOOD_COLOR.get(mood, "#6FE7FF"),
                            width=14, height=10)
            return
        key = mood if mood in self._sprites else _N
        photo = self._photo_cache.get(key)
        if photo is None:
            base = self._sprites.get(key) or self._sprites[_N]
            photo = (_pil_to_photo(base, self._art_h) if self._pil_mode
                     else _scale_to(base, self._art_h))
            self._photo_cache[key] = photo
        self.art.config(image=photo, text="")
        self.art.image = photo

    # ---- 好感度可视化 ----
    def _refresh_affection(self, delta: int = 0):
        from ..core.chat_engine import level_of
        aff = self.engine.affection
        name, hearts, lo, hi = level_of(aff)
        self.heart_lbl.config(text="♥" * hearts + "♡" * (6 - hearts))
        self.lvl_lbl.config(text=f"  好感度 {aff}/{AFFECTION_MAX} · {name}")
        if delta:
            sign = "+" if delta > 0 else ""
            self.delta_lbl.config(text=f"好感度 {sign}{delta}",
                                  fg=_HEART_ON if delta > 0 else _MUTED)
            self.win.after(2200, lambda: self.delta_lbl.config(text=""))
        # 进度条
        self.cv.delete("affbar")
        w = self.win.winfo_width() or 680
        x0, x1 = 18, w - 18
        seg = (aff - lo) / max(1, (hi - lo))
        self.cv.create_rectangle(x0, self._bar_y, x1, self._bar_y + 6,
                                 fill=_HEART_OFF, outline="", tags="affbar")
        self.cv.create_rectangle(x0, self._bar_y, x0 + (x1 - x0) * seg,
                                 self._bar_y + 6, fill=_HEART_ON, outline="",
                                 tags="affbar")
        if callable(self.on_affection):
            try:
                self.on_affection(aff)
            except Exception:
                pass

    # ---- 对话推进（伪多条 + 打字机）----
    def _push_turn(self, turn: ChatTurn):
        self._set_art(turn.mood)
        if turn.delta:
            self._refresh_affection(turn.delta)
        else:
            self._refresh_affection()
        self._queue = list(turn.lines)
        self._advance(first=True)

    def _advance(self, first: bool = False):
        # 若正在打字 → 一键补完当前句
        if self._type_job is not None:
            self.win.after_cancel(self._type_job)
            self._type_job = None
            self.text_lbl.config(text=self._typing)
            self._typed = len(self._typing)
            self._update_cont()
            return
        if not self._queue:
            return
        self._typing = self._queue.pop(0)
        self._typed = 0
        self._type_step()
        self._reset_idle()

    def _type_step(self):
        self._typed += 1
        self.text_lbl.config(text=self._typing[:self._typed])
        if self._typed < len(self._typing):
            self._type_job = self.win.after(_TYPE_MS, self._type_step)
        else:
            self._type_job = None
            self._update_cont()

    def _update_cont(self):
        self.cont_lbl.config(text="点击继续 ▼" if self._queue else "")

    # ---- 发送 / 自动对话（LLM 走后台线程，UI 永不冻结）----
    def _send(self):
        text = self.entry.get().strip()
        if not text or self._pending:
            return
        self.entry.delete(0, "end")
        self._pending = True
        self._queue = []
        if self._type_job is not None:
            try:
                self.win.after_cancel(self._type_job)
            except Exception:
                pass
            self._type_job = None
        self._start_thinking()
        threading.Thread(target=self._respond_bg, args=(text,),
                         daemon=True, name="chat-respond").start()

    def _respond_bg(self, text: str):
        try:
            turn = self.engine.respond(text)
        except Exception:
            turn = None
        try:
            self.win.after(0, lambda: self._on_responded(turn))
        except Exception:
            pass

    def _on_responded(self, turn):
        self._pending = False
        self._stop_thinking()
        if turn is not None and self.is_alive():
            self._push_turn(turn)

    def _start_thinking(self):
        self._think_dots = (self._think_dots % 3) + 1
        self.text_lbl.config(text="（想了想" + "…" * self._think_dots + "）")
        self.cont_lbl.config(text="")
        self._think_job = self.win.after(400, self._start_thinking)

    def _stop_thinking(self):
        if self._think_job is not None:
            try:
                self.win.after_cancel(self._think_job)
            except Exception:
                pass
            self._think_job = None

    def _reset_idle(self):
        if self._idle_job is not None:
            try:
                self.win.after_cancel(self._idle_job)
            except Exception:
                pass
        self._idle_job = self.win.after(_IDLE_MS, self._idle_talk)

    def _idle_talk(self):
        # 队列空闲、无打字、也不在等大模型回复时，才主动开口
        if not self._queue and self._type_job is None and not self._pending:
            self._push_turn(self.engine.idle_auto())
        else:
            self._reset_idle()

    # ---- 控制 ----
    def focus(self):
        try:
            self.win.deiconify()
            self.win.lift()
            self.win.focus_force()
            self.entry.focus_set()
        except Exception:
            pass

    def is_alive(self) -> bool:
        try:
            return bool(self.win.winfo_exists())
        except Exception:
            return False
