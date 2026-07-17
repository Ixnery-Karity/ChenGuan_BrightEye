"""运行模式与严格模式的逐级升级逻辑。

四种模式：
  - 陪伴模式 COMPANION：默认，悬浮桌宠 + 角色低打扰陪伴。
  - 严格模式 STRICT  ：不良用眼习惯一出现即弹窗，持续不改则逐级升级(颜色加深)。
  - 复盘模式 REVIEW  ：深度监测，一段专注结束后生成完整健康报告(原"20分钟报告")。
  - 勿扰模式 SILENT  ：仅后台记录，零弹窗零台词。

严格模式的"升级"由 StrictEscalator 维护：对每个不良类别累计持续秒数，
持续越久 → 严重度越高 → 提醒颜色由 青绿→琥珀→警示红 逐级加深。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class Mode(Enum):
    COMPANION = "companion"
    STRICT = "strict"
    REVIEW = "review"
    SILENT = "silent"


# 展示用元信息：名称 / 图标 / 一句话说明
MODE_META = {
    Mode.COMPANION: ("陪伴模式", "🐾", "悬浮桌宠 · 低打扰陪伴"),
    Mode.STRICT: ("严格模式", "🔥", "弹窗强提醒 · 逐级升级"),
    Mode.REVIEW: ("复盘模式", "📊", "深度监测 · 生成报告"),
    Mode.SILENT: ("勿扰模式", "🌙", "静默后台 · 只记录"),
}

MODE_ORDER = [Mode.COMPANION, Mode.STRICT, Mode.REVIEW, Mode.SILENT]


# 严重度等级 → 颜色（与 UI 调色一致：青绿→琥珀→警示红）
SEVERITY_COLORS = {
    0: "#2EE6A6",  # 正常/轻微 翠绿
    1: "#FFC94D",  # 持续 琥珀
    2: "#FF8A3D",  # 偏久 橙
    3: "#FF5277",  # 过久 警示红/缎带红
}

# 持续多少秒升一级（严格模式）
_STRICT_STEP_SEC = 12.0


@dataclass
class StrictAlert:
    """严格模式下的一条升级提醒。"""
    category: str          # eye / posture / distance / break
    severity: int          # 0..3，越大越严重
    color: str
    title: str
    detail: str


@dataclass
class StrictEscalator:
    """跟踪各不良类别的持续时间，输出逐级升级的提醒。"""
    _since: Dict[str, float] = field(default_factory=dict)  # 类别 → 该不良状态起始时刻
    _last_seen: Dict[str, float] = field(default_factory=dict)
    step_sec: float = _STRICT_STEP_SEC

    def _severity_for(self, started_at: float, now: float, base: int) -> int:
        held = now - started_at
        bonus = int(held // self.step_sec)
        return max(0, min(3, base + bonus))

    def update(self, advices, now: Optional[float] = None) -> List[StrictAlert]:
        """传入当前 AdviceEngine 的输出，返回带升级严重度的提醒列表。

        - 某类别持续不良：severity 随持续时间增长（颜色逐级加深）。
        - 类别恢复正常：清零其计时。
        """
        now = now or time.time()
        out: List[StrictAlert] = []
        active = set()

        for a in advices:
            cat = a.category
            active.add(cat)
            if cat not in self._since:
                self._since[cat] = now
            self._last_seen[cat] = now
            base = {"info": 0, "warn": 1, "alert": 2}.get(a.level.value, 1)
            sev = self._severity_for(self._since[cat], now, base)
            out.append(StrictAlert(
                category=cat,
                severity=sev,
                color=SEVERITY_COLORS[sev],
                title=a.title,
                detail=a.detail,
            ))

        # 清理已恢复的类别
        for cat in list(self._since.keys()):
            if cat not in active:
                self._since.pop(cat, None)
                self._last_seen.pop(cat, None)

        out.sort(key=lambda x: x.severity, reverse=True)
        return out

    def worst_severity(self, alerts: List[StrictAlert]) -> int:
        return max((a.severity for a in alerts), default=0)
