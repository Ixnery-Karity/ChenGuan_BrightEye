"""表情情绪分析 —— MediaPipe FaceLandmarker 的 52 维 blendshapes → 情绪标签。

设计取向（与本项目「离线优先、稳定可演示、可解释」一致）：
  · 零新增模型：直接复用已开启 output_face_blendshapes 的人脸模型输出；
  · 可解释管线：blendshapes → FACS 动作单元(AU) → Ekman 情绪原型加权打分，
    无需训练、每个判定都能溯源到具体肌肉动作，便于答辩讲解；
  · EMA 平滑各 AU 通道抗逐帧抖动 + 迟滞(hysteresis)去抖，避免情绪频繁跳变；
  · 情绪仅用于「桌宠关怀 / 复盘洞察」的柔性引导，绝不做医疗诊断。

FACS → 情绪的依据（Ekman 情绪原型 + FACS 动作单元编码，学界通行）：
  AU4 皱眉(降眉)、AU7 眼睑收紧、AU24 抿唇、AU23 唇紧 → 压力/专注紧张;
  AU1 内眉上扬、AU15 嘴角下拉、AU4 → 低落/悲伤;
  AU12 嘴角上扬、AU6 脸颊上提(杜氏真笑) → 积极;
  AU43/45 眼睑下垂/眨眼加重、AU26 张口(哈欠)、AU7 → 疲惫;
  各 AU 均低 → 平静。

情绪标签（与商业计划书「情绪管理 / 心理呵护」卖点对齐）：
  positive 积极 · neutral 平静 · tired 疲惫 · stressed 压力 · negative 低落
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple

# —— blendshape 通道 → FACS 动作单元(AU) 归约表 ——
# 每个 AU 取相关 blendshape 通道的均值（左右对称部位取均值更稳）。
_AU_CHANNELS: Dict[str, Tuple[str, ...]] = {
    "au1_inner_brow": ("browInnerUp",),                       # 内眉上扬 → 悲伤/担忧
    "au2_outer_brow": ("browOuterUpLeft", "browOuterUpRight"),  # 外眉上扬 → 惊讶
    "au4_brow_down": ("browDownLeft", "browDownRight"),       # 降眉/皱眉 → 紧张/愤怒
    "au5_lid_up": ("eyeWideLeft", "eyeWideRight"),            # 上睑上提 → 惊讶/警觉
    "au6_cheek": ("cheekSquintLeft", "cheekSquintRight"),     # 脸颊上提 → 杜氏真笑
    "au7_lid_tight": ("eyeSquintLeft", "eyeSquintRight"),     # 眼睑收紧 → 用力/疲劳
    "au9_nose": ("noseSneerLeft", "noseSneerRight"),          # 皱鼻 → 厌恶
    "au12_smile": ("mouthSmileLeft", "mouthSmileRight"),      # 嘴角上扬 → 愉悦
    "au15_frown": ("mouthFrownLeft", "mouthFrownRight"),      # 嘴角下拉 → 悲伤
    "au20_stretch": ("mouthStretchLeft", "mouthStretchRight"),  # 唇横拉 → 紧张/恐惧
    "au24_press": ("mouthPressLeft", "mouthPressRight"),      # 抿唇 → 压力/克制
    "au26_jaw": ("jawOpen",),                                 # 张口(哈欠) → 疲惫/惊讶
    "au45_blink": ("eyeBlinkLeft", "eyeBlinkRight"),          # 眨眼加重/闭合 → 疲惫
}

# —— Ekman 情绪原型：各情绪 = 相关 AU 的加权组合(正向促成 / 负向抑制) ——
# 权重取自 FACS 情绪编码惯例，经手工标定使 5 类在演示中区分清晰。
_PROTOTYPES: Dict[str, Dict[str, float]] = {
    # 积极：真笑(嘴角+脸颊)，皱眉/下拉会抑制
    "positive": {"au12_smile": 1.25, "au6_cheek": 0.55,
                 "au15_frown": -0.85, "au4_brow_down": -0.45},
    # 压力/专注紧张：皱眉 + 眼睑收紧 + 抿唇 + 唇横拉；微笑抑制
    "stressed": {"au4_brow_down": 1.05, "au7_lid_tight": 0.65,
                 "au24_press": 0.70, "au20_stretch": 0.45,
                 "au12_smile": -0.70},
    # 低落/悲伤：内眉上扬 + 嘴角下拉(+少量皱眉)；微笑/脸颊抑制
    "negative": {"au15_frown": 1.05, "au1_inner_brow": 0.85,
                 "au4_brow_down": 0.30, "au12_smile": -0.75,
                 "au6_cheek": -0.35},
    # 疲惫：眨眼加重 + 张口(哈欠) + 眼睑收紧；微笑/皱眉抑制(与压力区分)
    "tired": {"au45_blink": 0.85, "au26_jaw": 0.80, "au7_lid_tight": 0.60,
              "au12_smile": -0.55, "au4_brow_down": -0.35},
}

# 情绪中文名（展示用）
EMOTION_ZH = {
    "positive": "积极",
    "neutral": "平静",
    "tired": "疲惫",
    "stressed": "压力",
    "negative": "低落",
    "unknown": "未知",
}
# 需要弥悠主动关怀的负面情绪
CARE_EMOTIONS = {"stressed", "negative", "tired"}

# 护眼关怀优先级仲裁（Gemini 交叉评审建议合入）：
# 多种情绪同时超过基线且分差在迟滞裕度内（"情绪打架"）时，
# 作为护眼软件优先响应健康风险更高的一类：疲惫 > 压力 > 低落 > 积极。
_CARE_PRIORITY = ("tired", "stressed", "negative", "positive")


def _mean(shapes: Dict[str, float], keys) -> float:
    vals = [float(shapes.get(k, 0.0) or 0.0) for k in keys]
    return sum(vals) / len(vals) if vals else 0.0


class EmotionEstimator:
    """blendshapes → AU → Ekman 情绪原型打分 → 平滑/迟滞后的情绪标签。

    无脸/无系数时返回 None（调用方按无情绪处理）。同时暴露最近一次
    各情绪原型的置信分 `last_scores`，供关怀判定与复盘洞察使用。
    """

    def __init__(self, config):
        self.cfg = config
        self._ema: Dict[str, float] = {}      # 各 AU 通道的 EMA 状态
        self._label: Optional[str] = None     # 当前已提交(去抖后)的情绪
        self.last_scores: Dict[str, float] = {}

    def _smooth(self, name: str, value: float) -> float:
        a = self.cfg.smooth
        prev = self._ema.get(name)
        cur = value if prev is None else a * value + (1.0 - a) * prev
        self._ema[name] = cur
        return cur

    def _aus(self, blendshapes: Dict[str, float]) -> Dict[str, float]:
        return {au: self._smooth(au, _mean(blendshapes, chans))
                for au, chans in _AU_CHANNELS.items()}

    def estimate(self, blendshapes: Optional[Dict[str, float]]) -> Optional[str]:
        if not blendshapes:
            return None

        au = self._aus(blendshapes)
        # 各情绪原型打分（负分截断为 0，避免抑制项把分数拉成负数）
        scores = {
            emo: max(0.0, sum(w * au.get(k, 0.0) for k, w in proto.items()))
            for emo, proto in _PROTOTYPES.items()
        }
        self.last_scores = scores

        best = max(scores, key=scores.get)
        best_score = scores[best]
        # 优先级仲裁：与最高分之差在迟滞裕度内的情绪视为"并列"，
        # 按 疲惫>压力>低落>积极 取健康风险更高者，避免纯加权下情绪打架。
        margin = self.cfg.switch_margin
        for emo in _CARE_PRIORITY:
            if best_score - scores.get(emo, 0.0) <= margin:
                best = emo
                best_score = scores[emo]
                break
        # 未超过平静基线 → 平静
        candidate = best if best_score >= self.cfg.neutral_bias else "neutral"

        # 迟滞去抖：与当前情绪不同时，需高出当前情绪足够裕度才切换
        if candidate != self._label and self._label is not None:
            cur_score = scores.get(self._label, 0.0)
            if self._label != "neutral" and best_score < cur_score + self.cfg.switch_margin:
                candidate = self._label
        self._label = candidate
        return candidate

    def care_score(self) -> float:
        """当前负面情绪(压力/低落/疲惫)的最高置信分，供关怀触发做门限。"""
        return max((self.last_scores.get(e, 0.0) for e in CARE_EMOTIONS),
                   default=0.0)

    @staticmethod
    def zh(label: Optional[str]) -> str:
        return EMOTION_ZH.get(label or "unknown", "未知")
