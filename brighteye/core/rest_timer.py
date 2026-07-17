"""20-20-20 护眼法则与连续用眼计时。

20-20-20：每用眼 20 分钟，远眺 6 米外 20 秒。
另对"连续用眼超过 45 分钟"给出强休息提醒。
'用眼'以检测到人脸为准；离开座位会暂停计时。
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class RestState:
    continuous_use_sec: float = 0.0     # 当前这段连续用眼时长
    screen_time_sec: float = 0.0        # 本会话累计用眼时长
    since_last_break_sec: float = 0.0    # 距上次远眺提醒
    due_microbreak: bool = False        # 该远眺了(20分钟节律)
    due_longbreak: bool = False         # 连续用眼过久，强提醒


class RestTimer:
    def __init__(self, thresholds):
        self.t = thresholds
        self.state = RestState()
        self._last = time.time()

    def update(self, face_present: bool, now: float | None = None) -> RestState:
        now = now or time.time()
        dt = max(0.0, now - self._last)
        self._last = now

        if face_present:
            self.state.continuous_use_sec += dt
            self.state.screen_time_sec += dt
            self.state.since_last_break_sec += dt
        else:
            # 离开座位即视为短暂休息，连续计时清零
            if self.state.continuous_use_sec > 0:
                self.state.continuous_use_sec = 0.0

        self.state.due_microbreak = (
            self.state.since_last_break_sec >= self.t.break_interval_min * 60
        )
        self.state.due_longbreak = (
            self.state.continuous_use_sec >= self.t.continuous_use_warn_min * 60
        )
        return self.state

    def acknowledge_break(self) -> None:
        """用户完成远眺后调用，重置节律。"""
        self.state.since_last_break_sec = 0.0
        self.state.due_microbreak = False
        self.state.continuous_use_sec = 0.0
        self.state.due_longbreak = False
