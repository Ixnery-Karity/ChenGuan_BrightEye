"""模拟视觉后端。

无摄像头 / 无 mediapipe 环境下，生成接近真实分布的用眼数据，
让整套指标—建议—报告链路与 GUI 完整跑通，用于答辩演示。

模拟一个"长时间盯屏导致眨眼下降、坐姿逐渐前倾"的典型场景，
以便现场演示告警与建议的触发。
"""

from __future__ import annotations

import math
import random
import time

from .metrics import FrameSample


class SimulatedVisionBackend:
    def __init__(self, seed: int | None = None, time_scale: float = 1.0):
        self.rng = random.Random(seed)
        self.time_scale = time_scale  # >1 加速演示（让告警更快出现）
        self._t0 = time.time()
        self._closing_frames = 0  # 当前眨眼尚未结束的闭眼帧数

    @staticmethod
    def available() -> bool:
        return True

    def read(self, thresholds, timestamp: float) -> FrameSample:
        # 虚拟"已用眼"时长（可加速）
        elapsed = (timestamp - self._t0) * self.time_scale

        # 眨眼频率随用眼时间衰减：从 ~17 次/分 缓慢降到 ~6 次/分
        target_rate = max(6.0, 17.0 - elapsed / 90.0)
        # 一次眨眼持续 2-3 帧（约 130-200ms@15fps），EAR 才会形成可检测的下陷
        if self._closing_frames > 0:
            self._closing_frames -= 1
            ear = 0.12
        else:
            per_frame_p = (target_rate / 60.0) / 15.0
            if self.rng.random() < per_frame_p:
                self._closing_frames = self.rng.choice([2, 3]) - 1
                ear = 0.12
            else:
                ear = self.rng.uniform(0.26, 0.33)

        # 坐姿：颅椎角随时间从 ~58° 前倾到 ~40°，叠加自然抖动
        cva = 58.0 - min(20.0, elapsed / 40.0) + self.rng.uniform(-3, 3)
        shoulder_tilt = abs(self.rng.gauss(4.0, 2.5))

        # 用眼距离：均值随疲劳越靠越近
        base_dist = 58.0 - min(15.0, elapsed / 60.0)
        distance = base_dist + self.rng.uniform(-4, 4)

        # 偶尔"离开座位"（无人脸）
        face_present = self.rng.random() > 0.02

        return FrameSample(
            timestamp=timestamp,
            ear=ear if face_present else None,
            is_blink_event=False,  # 由 BlinkCounter 统一判定
            cva=round(cva, 1) if face_present else None,
            shoulder_tilt=round(shoulder_tilt, 1) if face_present else None,
            distance_cm=round(distance, 1) if face_present else None,
            face_present=face_present,
            blendshapes=self._fake_blendshapes(elapsed) if face_present else None,
        )

    def _fake_blendshapes(self, elapsed: float) -> dict:
        """模拟 52 维表情系数子集：随用眼时间从「平静」渐转「疲惫/压力」，
        让无摄像头环境也能演示 AU→情绪分析与文乃关怀触发。
        覆盖新情绪引擎所需的 AU 通道（眨眼加重/张口哈欠/眼睑收紧/皱眉等）。"""
        fatigue = min(1.0, elapsed / 240.0)   # 0→1 随时长上升
        j = lambda s=0.05: self.rng.uniform(-s, s)  # noqa: E731  小抖动
        squint = max(0.0, 0.15 + 0.45 * fatigue + j())     # AU7 眼睑收紧
        brow = max(0.0, 0.08 + 0.30 * fatigue + j())       # AU4 皱眉(专注/压力)
        blink = max(0.0, 0.10 + 0.45 * fatigue + j())      # AU45 眨眼加重(疲惫)
        # 哈欠：疲劳越高越频繁且张得越大
        yawn = self.rng.random() < (0.05 + 0.20 * fatigue)
        jaw = round(0.45 + j(0.1), 3) if yawn else round(0.05 + j(0.03), 3)
        smile = max(0.0, 0.30 - 0.25 * fatigue + j())      # AU12 逐渐减弱
        return {
            "eyeSquintLeft": round(squint, 3), "eyeSquintRight": round(squint, 3),
            "eyeBlinkLeft": round(blink, 3), "eyeBlinkRight": round(blink, 3),
            "browDownLeft": round(brow, 3), "browDownRight": round(brow, 3),
            "browInnerUp": round(max(0.0, 0.05 + 0.15 * fatigue + j()), 3),
            "cheekSquintLeft": round(max(0.0, 0.5 * smile), 3),
            "cheekSquintRight": round(max(0.0, 0.5 * smile), 3),
            "mouthSmileLeft": round(smile, 3), "mouthSmileRight": round(smile, 3),
            "mouthFrownLeft": 0.05, "mouthFrownRight": 0.05,
            "mouthPressLeft": round(max(0.0, 0.10 * fatigue), 3),
            "mouthPressRight": round(max(0.0, 0.10 * fatigue), 3),
            "jawOpen": jaw,
        }
