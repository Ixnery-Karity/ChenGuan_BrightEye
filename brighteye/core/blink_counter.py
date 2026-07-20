"""把逐帧 EAR 序列转换为离散的眨眼事件（状态机）。

判据 = 帧数 + 墙钟时间双兜底：正常帧率下沿用「连续 N 帧低于阈值」；
UI 卡顿/掉帧导致快速眨眼只覆盖 1 帧时，只要闭眼墙钟时长达到
min_close_sec 仍计一次眨眼，避免漏检。
"""

from __future__ import annotations

import time


class BlinkCounter:
    def __init__(self, ear_threshold: float, consec_frames: int,
                 min_close_sec: float = 0.10):
        self.ear_threshold = ear_threshold
        self.consec_frames = consec_frames
        self.min_close_sec = min_close_sec
        self._below = 0
        self._below_since: float | None = None

    def update(self, ear: float | None, now: float | None = None) -> bool:
        """返回 True 表示本帧完成了一次完整眨眼（睁→闭→睁）。"""
        if ear is None:
            return False
        if now is None:
            now = time.time()
        if ear < self.ear_threshold:
            self._below += 1
            if self._below_since is None:
                self._below_since = now
            return False
        # EAR 回到阈值以上：帧数够 或 闭眼墙钟时长够（抗掉帧）即计一次眨眼
        closed_sec = (now - self._below_since) if self._below_since else 0.0
        triggered = (self._below >= self.consec_frames
                     or (self._below >= 1 and closed_sec >= self.min_close_sec))
        self._below = 0
        self._below_since = None
        return triggered
