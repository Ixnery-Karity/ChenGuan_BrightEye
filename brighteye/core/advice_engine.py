"""规则化健康建议引擎。

输入实时指标，输出分级（info/warn/alert）的行为建议。
合规红线：只做"行为引导/就医提示"，不做诊断与处方。
涉及药物处仅以"可咨询医生/药师"的提示形式出现。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class Level(Enum):
    INFO = "info"
    WARN = "warn"
    ALERT = "alert"


@dataclass
class Advice:
    level: Level
    category: str        # eye / posture / distance / break
    title: str
    detail: str


class AdviceEngine:
    def __init__(self, thresholds):
        self.t = thresholds

    def evaluate(
        self,
        blink_rate: float,
        avg_cva: Optional[float],
        avg_tilt: Optional[float],
        avg_distance: Optional[float],
        rest_state,
    ) -> List[Advice]:
        out: List[Advice] = []

        # —— 眨眼 / 干眼 ——
        if blink_rate and blink_rate < self.t.blink_rate_low:
            out.append(Advice(
                Level.ALERT, "eye", "眨眼偏少，干眼风险升高",
                f"近一分钟约 {blink_rate:.0f} 次/分，低于健康参考值"
                f"（{self.t.blink_rate_normal:.0f} 次/分）。建议有意识完整眨眼、"
                "适度增加眨眼频率；眼干明显时可咨询医生或药师是否使用人工泪液类产品。",
            ))
        elif blink_rate and blink_rate < self.t.blink_rate_normal:
            out.append(Advice(
                Level.WARN, "eye", "眨眼频率略低",
                f"约 {blink_rate:.0f} 次/分，建议主动多眨眼、保持眼表湿润。",
            ))

        # —— 坐姿 / 体态 ——
        if avg_cva is not None and avg_cva < self.t.cva_warning:
            out.append(Advice(
                Level.ALERT, "posture", "检测到明显低头/前倾",
                "颈部前倾会显著增加颈椎负荷。建议抬高屏幕使上缘与眼睛大致齐平、"
                "背部贴紧椅背、下颌微收。",
            ))
        elif avg_cva is not None and avg_cva < self.t.cva_good:
            out.append(Advice(
                Level.WARN, "posture", "坐姿可优化",
                "上半身略有前倾，注意挺直背部、放松肩颈。",
            ))
        if avg_tilt is not None and avg_tilt > self.t.shoulder_tilt_max:
            out.append(Advice(
                Level.WARN, "posture", "双肩高低不均",
                f"高低肩角度约 {avg_tilt:.0f}°，建议双肩放平、避免长期单侧用力。",
            ))

        # —— 用眼距离 ——
        if avg_distance is not None and avg_distance < self.t.distance_min_cm:
            out.append(Advice(
                Level.WARN, "distance", "用眼距离偏近",
                f"当前约 {avg_distance:.0f} cm，建议保持 "
                f"{self.t.distance_ideal_cm:.0f} cm 左右的舒适距离。",
            ))

        # —— 时间管理 ——
        if rest_state.due_longbreak:
            out.append(Advice(
                Level.ALERT, "break", "连续用眼过久，请立即休息",
                f"已连续用眼超过 {self.t.continuous_use_warn_min:.0f} 分钟，"
                "建议起身活动 3-5 分钟。",
            ))
        elif rest_state.due_microbreak:
            out.append(Advice(
                Level.INFO, "break", "该远眺啦（20-20-20）",
                f"请眺望 {self.t.break_look_far_distance_m} 米外约 "
                f"{self.t.break_look_far_sec} 秒，放松睫状肌。",
            ))

        return out
