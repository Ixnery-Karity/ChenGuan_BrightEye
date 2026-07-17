"""Tkinter Canvas 粒子背景（电竞导播风：浮动粒子 + 星座连线 + 状态变色）。

纯标准库实现，零额外依赖。性能友好：粒子数量有限、仅在可视区更新。
mood 改变时整体色调平滑过渡（健康=绿色平稳，告警=红色加速）。
低配机自适应：step() 内测量帧耗时(EMA)，持续超预算自动减粒子
（不低于 min_count），机器空闲后再逐步恢复——保证 UI 永不卡顿。
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass
from typing import List, Tuple

# 帧耗时预算：粒子绘制在 30fps 主循环里只允许占这么多毫秒
_COST_HIGH_MS = 14.0    # EMA 高于此值 → 减粒子
_COST_LOW_MS = 6.0      # EMA 低于此值 → 逐步恢复
_ADJUST_EVERY = 45      # 每隔多少帧才调整一次，避免抖动


def _hex(rgb: Tuple[int, int, int]) -> str:
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(c))) for c in rgb)


def _lerp(a, b, t):
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


@dataclass
class _P:
    x: float
    y: float
    vx: float
    vy: float
    r: float


class ParticleField:
    """在指定 Canvas 上绘制并驱动粒子背景。

    用法：每帧调用 step()；状态变化时调用 set_mood((r,g,b), speed)。
    """

    def __init__(self, canvas, width: int, height: int, count: int = 46,
                 min_count: int = 16):
        self.c = canvas
        self.w = width
        self.h = height
        self.base_count = count              # 目标粒子数（性能允许时恢复到它）
        self.min_count = max(4, min(min_count, count))
        self.base_color = (46, 230, 166)     # 当前色(渐变目标用)
        self.target_color = (46, 230, 166)
        self.speed = 1.0
        self.target_speed = 1.0
        self.link_dist = 132.0
        self._ids: List[int] = []
        self._cost_ms = 0.0                  # step() 耗时 EMA(毫秒)
        self._steps = 0
        self.ps: List[_P] = []
        random.seed(7)
        for _ in range(count):
            self.ps.append(self._spawn())

    def _spawn(self) -> _P:
        ang = random.uniform(0, math.tau)
        spd = random.uniform(0.15, 0.6)
        return _P(
            x=random.uniform(0, self.w),
            y=random.uniform(0, self.h),
            vx=math.cos(ang) * spd,
            vy=math.sin(ang) * spd,
            r=random.uniform(1.3, 3.2),
        )

    def resize(self, w: int, h: int) -> None:
        self.w, self.h = max(1, w), max(1, h)

    def set_mood(self, rgb: Tuple[int, int, int], speed: float = 1.0) -> None:
        self.target_color = rgb
        self.target_speed = speed

    def step(self) -> None:
        t0 = time.perf_counter()
        # 颜色 / 速度平滑过渡
        self.base_color = _lerp(self.base_color, self.target_color, 0.06)
        self.speed += (self.target_speed - self.speed) * 0.08
        col = _hex(self.base_color)
        dim = _hex(_lerp(self.base_color, (10, 14, 26), 0.55))

        c = self.c
        for i in self._ids:
            c.delete(i)
        self._ids.clear()

        # 连线（星座感）——先画线再画点
        n = len(self.ps)
        ld2 = self.link_dist * self.link_dist
        for i in range(n):
            p = self.ps[i]
            p.x += p.vx * self.speed
            p.y += p.vy * self.speed
            if p.x < -10:
                p.x = self.w + 10
            elif p.x > self.w + 10:
                p.x = -10
            if p.y < -10:
                p.y = self.h + 10
            elif p.y > self.h + 10:
                p.y = -10

        for i in range(n):
            a = self.ps[i]
            for j in range(i + 1, n):
                b = self.ps[j]
                dx = a.x - b.x
                dy = a.y - b.y
                d2 = dx * dx + dy * dy
                if d2 < ld2:
                    self._ids.append(
                        c.create_line(a.x, a.y, b.x, b.y, fill=dim, width=1)
                    )

        for p in self.ps:
            self._ids.append(
                c.create_oval(p.x - p.r, p.y - p.r, p.x + p.r, p.y + p.r,
                              fill=col, outline="")
            )

        # —— 自适应降载：低配机上帧耗时超预算 → 减粒子；空闲 → 恢复 ——
        self._cost_ms += ((time.perf_counter() - t0) * 1000.0 - self._cost_ms) * 0.1
        self._steps += 1
        if self._steps % _ADJUST_EVERY == 0:
            if self._cost_ms > _COST_HIGH_MS and len(self.ps) > self.min_count:
                # 一次砍 15%，快速止损（连线是 O(n²)，减粒子收益平方级）
                drop = max(1, int(len(self.ps) * 0.15))
                del self.ps[-drop:]
            elif self._cost_ms < _COST_LOW_MS and len(self.ps) < self.base_count:
                for _ in range(min(4, self.base_count - len(self.ps))):
                    self.ps.append(self._spawn())

    def clear(self) -> None:
        for i in self._ids:
            self.c.delete(i)
        self._ids.clear()
