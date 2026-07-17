"""把逐帧 EAR 序列转换为离散的眨眼事件（状态机）。"""

from __future__ import annotations


class BlinkCounter:
    def __init__(self, ear_threshold: float, consec_frames: int):
        self.ear_threshold = ear_threshold
        self.consec_frames = consec_frames
        self._below = 0
        self._closed = False

    def update(self, ear: float | None) -> bool:
        """返回 True 表示本帧完成了一次完整眨眼（睁→闭→睁）。"""
        if ear is None:
            return False
        if ear < self.ear_threshold:
            self._below += 1
            return False
        # EAR 回到阈值以上：若此前已连续闭眼足够帧，则计一次眨眼
        triggered = self._below >= self.consec_frames
        self._below = 0
        return triggered
