"""极端用眼守护 —— 在「极端不正常用眼」持续发生时强制干预。

判据（需同时/持续满足，模拟真正极端场景，避免误触普通轻度不良）：
  · 连续用眼时长超过阈值；且
  · 用眼距离持续过近；且
  · 眨眼率极低（远低于干眼阈值）。
上述极端状态持续 `trigger_sustain_sec` 秒才触发一次干预，并带冷却时间。

两档强度（配置 `guardian.mode`）：
  · soft（默认）：全屏半透明遮罩 + 弥悠立绘 + 关怀台词 + 强制休息倒计时，
    纯 UI、零系统风险、可随时演示；
  · hard：调用 Windows `LockWorkStation()` 真系统锁屏；非 Windows 自动降级 soft。

本模块只做「判定」，具体弹遮罩/锁屏由 UI 层执行（保持核心与 UI 解耦）。
判定与 UI 之间通过 `GuardAction` 数据传递。
"""

from __future__ import annotations

import platform
import time
from dataclasses import dataclass
from typing import Optional


@dataclass
class GuardAction:
    """一次强制干预指令，交给 UI 执行。"""
    mode: str                 # 实际执行档位：soft | hard
    reason: str               # 触发原因（展示 + 记录）
    force_rest_sec: int       # soft 遮罩强制休息倒计时
    line: str                 # 弥悠的关怀台词


# soft 遮罩上弥悠的话（傲娇但强硬，强制你休息）
_GUARD_LINES = [
    "够了！你这用眼方式太离谱了——现在，强制休息！不许喊停！",
    "笨蛋！再这样下去眼睛真的要坏掉了…给我停下来，乖乖歇着！",
    "我可是认真的哦。这次不听我的不行——闭眼，远眺，休息！",
]


class ScreenGuardian:
    """极端用眼判定器。UI 每帧把 Snapshot 交给 evaluate()，返回非 None 即执行。"""

    def __init__(self, config):
        self.cfg = config.guardian
        self._extreme_since: Optional[float] = None   # 极端状态起始时刻
        self._last_trigger_t: float = 0.0             # 上次触发时刻（冷却用）
        self._rng_i = 0

    def _is_extreme(self, snap) -> bool:
        c = self.cfg
        # 连续用眼过久
        if snap.continuous_use_min < c.trigger_continuous_use_min:
            return False
        # 距离过近（需有距离读数）
        if snap.distance is None or snap.distance >= c.trigger_distance_cm:
            return False
        # 眨眼率极低
        if snap.blink_rate is None or snap.blink_rate >= c.trigger_blink_rate:
            return False
        return True

    def evaluate(self, snap) -> Optional[GuardAction]:
        """返回 GuardAction 表示应立即干预；否则 None。"""
        if not getattr(self.cfg, "enabled", True):
            return None
        now = time.time()

        if not self._is_extreme(snap):
            self._extreme_since = None
            return None

        # 极端状态需持续足够久
        if self._extreme_since is None:
            self._extreme_since = now
            return None
        if (now - self._extreme_since) < self.cfg.trigger_sustain_sec:
            return None

        # 冷却：两次干预至少间隔 cooldown_sec，避免连环打扰
        if (now - self._last_trigger_t) < self.cfg.cooldown_sec:
            return None

        self._last_trigger_t = now
        self._extreme_since = None   # 触发后重新计时

        mode = self._effective_mode()
        line = _GUARD_LINES[self._rng_i % len(_GUARD_LINES)]
        self._rng_i += 1
        reason = (
            f"连续用眼 {snap.continuous_use_min:.0f} 分钟、"
            f"距离仅 {snap.distance:.0f}cm、"
            f"眨眼率低至 {snap.blink_rate:.0f} 次/分 —— 已达极端用眼阈值"
        )
        return GuardAction(mode=mode, reason=reason,
                           force_rest_sec=int(self.cfg.force_rest_sec), line=line)

    def _effective_mode(self) -> str:
        """hard 仅在 Windows 生效；其它平台自动降级 soft。"""
        mode = getattr(self.cfg, "mode", "soft")
        if mode == "hard" and platform.system() != "Windows":
            return "soft"
        return mode

    @staticmethod
    def system_lock() -> bool:
        """真系统锁屏（仅 Windows）。成功返回 True，否则 False。"""
        if platform.system() != "Windows":
            return False
        try:
            import ctypes
            ctypes.windll.user32.LockWorkStation()
            return True
        except Exception:
            return False
