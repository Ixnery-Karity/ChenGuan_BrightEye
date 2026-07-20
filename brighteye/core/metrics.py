"""会话指标采集与统计。

monitor 每帧产生一个 FrameSample，SessionMetrics 负责累计并给出
实时统计（眨眼频率、平均坐姿、用眼距离、屏幕时长等）。
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Optional


@dataclass
class FrameSample:
    """单帧检测结果。任一字段为 None 表示该指标本帧不可用。"""
    timestamp: float
    ear: Optional[float] = None              # 眼纵横比
    is_blink_event: bool = False             # 本帧是否触发一次眨眼计数
    cva: Optional[float] = None              # 颅椎角(近似)，越大越直
    shoulder_tilt: Optional[float] = None    # 高低肩角度
    distance_cm: Optional[float] = None      # 估算用眼距离
    face_present: bool = True                 # 是否检测到人脸
    blendshapes: Optional[Dict[str, float]] = None  # 52维表情系数(可选)
    emotion: Optional[str] = None            # 情绪标签(由 EmotionEstimator 填充)


@dataclass
class SessionMetrics:
    started_at: float = field(default_factory=time.time)
    blink_count: int = 0
    frames: int = 0
    face_frames: int = 0

    # 滚动窗口：眨眼保留近 5 分钟事件以支持可调窗口；
    # 姿态/距离存 (timestamp, value)：avg_* 供报告(约20s窗)，
    # recent_* 按墙钟时间取短窗（~3s）供实时显示（不受帧率波动影响）。
    _blink_times: Deque[float] = field(default_factory=lambda: deque(maxlen=600))
    _cva: Deque[tuple] = field(default_factory=lambda: deque(maxlen=300))
    _tilt: Deque[tuple] = field(default_factory=lambda: deque(maxlen=300))
    _dist: Deque[tuple] = field(default_factory=lambda: deque(maxlen=300))

    # 累计的"不良状态"秒数，用于报告
    bad_posture_seconds: float = 0.0
    too_close_seconds: float = 0.0
    last_update: float = field(default_factory=time.time)

    # 情绪时间线：各情绪标签累计秒数 + 最近情绪采样(供复盘分析)
    emotion_seconds: Dict[str, float] = field(default_factory=dict)
    _emotion_samples: Deque[tuple] = field(default_factory=lambda: deque(maxlen=600))

    # —— 报告图表数据（趋势图/热力图/风险时段）——
    # 趋势时间线：每 _TL_STEP 秒采一点 (t, blink_rate, distance, cva)
    timeline: list = field(default_factory=list)
    _tl_last: float = 0.0
    # 分小时负荷：hour(0-23) → {"use": 用眼秒, "bad": 不良状态秒}
    hour_load: Dict[int, Dict[str, float]] = field(default_factory=dict)
    # 连续用眼段：[(start_ts, end_ts), ...]，离席/告一段落(>3min 无脸)即封段
    use_segments: list = field(default_factory=list)
    _seg_start: Optional[float] = None
    _seg_last: Optional[float] = None

    # 实时眨眼频率的指数平滑状态
    _blink_rate_ema: Optional[float] = None

    def add(self, s: FrameSample, thresholds) -> None:
        now = s.timestamp
        dt = max(0.0, now - self.last_update)
        self.last_update = now
        self.frames += 1

        if s.face_present:
            self.face_frames += 1

        if s.is_blink_event:
            self.blink_count += 1
            self._blink_times.append(now)

        if s.cva is not None:
            self._cva.append((now, s.cva))
            if s.cva < thresholds.cva_warning:
                self.bad_posture_seconds += dt
        if s.shoulder_tilt is not None:
            self._tilt.append((now, s.shoulder_tilt))
        if s.distance_cm is not None:
            self._dist.append((now, s.distance_cm))
            if s.distance_cm < thresholds.distance_min_cm:
                self.too_close_seconds += dt

        if s.emotion:
            self.emotion_seconds[s.emotion] = self.emotion_seconds.get(s.emotion, 0.0) + dt
            self._emotion_samples.append((now, s.emotion))

        # —— 报告图表数据累计 ——
        self._track_charts(s, now, dt, thresholds)

    _TL_STEP = 10.0        # 趋势时间线采样间隔(秒)
    _SEG_GAP = 180.0       # 离席超过此秒数则封闭一个"连续用眼段"

    def _track_charts(self, s: FrameSample, now: float, dt: float, thresholds) -> None:
        """累计报告图表所需数据：趋势时间线、分时段负荷、连续用眼段。"""
        # 1) 趋势时间线（10 秒一点，取当前窗口值/最近观测值）
        if now - self._tl_last >= self._TL_STEP:
            self._tl_last = now
            self.timeline.append((
                round(now, 1),
                round(self.blink_rate_recent(30.0), 1),
                round(self._dist[-1][1], 1) if self._dist else None,
                round(self._cva[-1][1], 1) if self._cva else None,
            ))
        # 2) 分小时用眼/不良负荷（热力图·风险时段）
        if s.face_present and dt > 0:
            hour = time.localtime(now).tm_hour
            rec = self.hour_load.setdefault(hour, {"use": 0.0, "bad": 0.0})
            rec["use"] += dt
            bad = ((s.cva is not None and s.cva < thresholds.cva_warning)
                   or (s.distance_cm is not None
                       and s.distance_cm < thresholds.distance_min_cm))
            if bad:
                rec["bad"] += dt
        # 3) 连续用眼段（离席 >3 分钟封段，供"XX点连续用眼N分钟无休息"标注）
        if s.face_present:
            if self._seg_start is None:
                self._seg_start = now
            elif self._seg_last is not None and now - self._seg_last > self._SEG_GAP:
                self.use_segments.append((self._seg_start, self._seg_last))
                self._seg_start = now
            self._seg_last = now

    def finish_segments(self) -> list:
        """封闭当前进行中的用眼段并返回全部段落（报告生成时调用）。"""
        segs = list(self.use_segments)
        if self._seg_start is not None and self._seg_last is not None \
                and self._seg_last > self._seg_start:
            segs.append((self._seg_start, self._seg_last))
        return segs

    # ---- 实时统计 ----------------------------------------------------
    @property
    def elapsed_sec(self) -> float:
        return max(1e-6, time.time() - self.started_at)

    @property
    def elapsed_min(self) -> float:
        return self.elapsed_sec / 60.0

    def blink_rate_recent(self, window_sec: float = 30.0) -> float:
        """近 window_sec 秒折算成的每分钟眨眼次数（滚动窗口，响应当前状态）。"""
        now = time.time()
        recent = [t for t in self._blink_times if now - t <= window_sec]
        span = min(window_sec, self.elapsed_sec)
        if span < 1e-6:
            return 0.0
        return len(recent) * 60.0 / span

    def blink_rate_realtime(self, window_sec: float = 30.0, alpha: float = 0.2) -> float:
        """实时眨眼频率：滚动窗口原始值再做指数平滑，兼顾灵敏度与稳定性。

        - 窗口越短越能反映"当下"用眼状态（而非整段会话累加）；
        - EMA 平滑消除单次眨眼进出窗口造成的台阶抖动，不引入明显滞后。
        """
        raw = self.blink_rate_recent(window_sec)
        if self._blink_rate_ema is None:
            self._blink_rate_ema = raw
        else:
            self._blink_rate_ema += alpha * (raw - self._blink_rate_ema)
        return self._blink_rate_ema

    def blink_rate_avg(self) -> float:
        """整段会话的累计平均眨眼频率（仅用于结束后的健康报告）。"""
        return self.blink_count / self.elapsed_min if self.elapsed_min else 0.0

    @staticmethod
    def _avg(d: Deque[tuple]) -> Optional[float]:
        return sum(v for _, v in d) / len(d) if d else None

    @staticmethod
    def _recent(d: Deque[tuple], window_sec: float) -> Optional[float]:
        """近 window_sec 秒内样本的均值；无样本时回退最近一次观测值。

        按墙钟时间截窗，帧率波动/卡顿不会拉长实际窗口，保证实时性。
        """
        if not d:
            return None
        now = time.time()
        vals = [v for t, v in d if now - t <= window_sec]
        if vals:
            return sum(vals) / len(vals)
        return d[-1][1]

    def avg_cva(self) -> Optional[float]:
        return self._avg(self._cva)

    def avg_tilt(self) -> Optional[float]:
        return self._avg(self._tilt)

    def avg_distance(self) -> Optional[float]:
        return self._avg(self._dist)

    def recent_cva(self, window_sec: float = 3.0) -> Optional[float]:
        """实时颅椎角：近几秒短窗均值（姿势变化秒级反映到界面）。"""
        return self._recent(self._cva, window_sec)

    def recent_tilt(self, window_sec: float = 3.0) -> Optional[float]:
        return self._recent(self._tilt, window_sec)

    def recent_distance(self, window_sec: float = 3.0) -> Optional[float]:
        """实时用眼距离：近几秒短窗均值（前倾靠近立刻可见）。"""
        return self._recent(self._dist, window_sec)

    def face_ratio(self) -> float:
        return self.face_frames / self.frames if self.frames else 0.0

    def dominant_emotion(self) -> Optional[str]:
        """整段会话累计时长最长的情绪标签（复盘用）。"""
        if not self.emotion_seconds:
            return None
        return max(self.emotion_seconds.items(), key=lambda kv: kv[1])[0]

    def emotion_distribution(self) -> Dict[str, float]:
        """各情绪占比(0~1)，供复盘报告与大模型行为洞察。"""
        total = sum(self.emotion_seconds.values())
        if total <= 0:
            return {}
        return {k: v / total for k, v in self.emotion_seconds.items()}
