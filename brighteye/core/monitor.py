"""核心编排器：把视觉后端、指标统计、计时、建议串成一条流水线。

monitor 与 UI 解耦：UI 只需周期性调用 `tick()` 拿到最新快照，
或注册回调。后端选择策略：
  1. 优先真实摄像头 (RealVisionBackend, 需 mediapipe+opencv+摄像头)
  2. 回退到模拟器 (SimulatedVisionBackend)，保证任意环境可演示
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List, Optional

from ..config import AppConfig
from .advice_engine import Advice, AdviceEngine
from .blink_counter import BlinkCounter
from .emotion import EmotionEstimator
from .guardian import GuardAction, ScreenGuardian
from .metrics import FrameSample, SessionMetrics
from .modes import Mode, StrictAlert, StrictEscalator
from .persona import Persona, SLEEPY, HAPPY, NORMAL, start_line_refresher
from .rest_timer import RestTimer
from .simulator import SimulatedVisionBackend
from .system_watch import FullscreenWatcher

_SEV_RANK = {"alert": 2, "warn": 1, "info": 0}


@dataclass
class Snapshot:
    """供 UI 渲染的一帧实时状态。"""
    backend: str
    face_present: bool
    blink_rate: float
    blink_total: int
    cva: Optional[float]
    distance: Optional[float]
    screen_time_min: float
    continuous_use_min: float
    next_break_in_sec: float
    advices: List[Advice]
    # —— 模式 / 桌宠情绪 / 严格升级 ——
    mode: str = "companion"
    mood: str = "normal"
    persona_line: Optional[str] = None      # 仅在需要新台词时非空，UI 据此弹气泡
    worst_severity: int = 0
    strict_alerts: List[StrictAlert] = field(default_factory=list)
    auto_report_path: Optional[str] = None  # 复盘模式到点自动出的报告路径
    # —— 表情情绪 / 极端用眼守护 / 游戏勿扰 ——
    emotion: Optional[str] = None           # 当前情绪标签(positive/neutral/tired/stressed/negative)
    guard_action: Optional[GuardAction] = None  # 非空表示 UI 需立即弹遮罩/锁屏
    game_mode: bool = False                 # 前台全屏独占(游戏/放映) → 自动勿扰中


class Monitor:
    def __init__(self, config: AppConfig, force_simulate: bool = False,
                 sim_time_scale: float = 1.0, sim_seed: int | None = None,
                 camera_index: int = 0, use_process: bool = False):
        self.config = config
        self.t = config.thresholds
        self.metrics = SessionMetrics()
        self.rest = RestTimer(self.t)
        self.advice = AdviceEngine(self.t)
        self.blink = BlinkCounter(self.t.ear_blink, self.t.ear_consec_frames)

        self.backend_name = "模拟数据"
        self.fallback_reason = None  # 实时后端失败时记录原因，供上层提示
        self._real = None
        self._cap = None
        self._sim = SimulatedVisionBackend(seed=sim_seed, time_scale=sim_time_scale)
        # —— 多进程视觉后端（建议3：性能隔离，--mp-vision 开启）——
        self._use_process = use_process
        self._worker = None            # VisionWorker 句柄（子进程模式）
        self._last_ws = None           # 子进程回传的最近一帧样本（无新帧时沿用）
        # —— 游戏勿扰（建议4：前台全屏独占自动免打扰）——
        self._fs_watch = (FullscreenWatcher(config.system.game_poll_sec)
                          if config.system.game_mode_enabled else None)
        self.game_mode = False

        # —— 模式 / 角色 / 严格升级 ——
        try:
            self.mode = Mode(config.default_mode)
        except ValueError:
            self.mode = Mode.COMPANION
        self.persona = Persona()
        self._escalator = StrictEscalator()
        # —— 表情情绪 / 极端用眼守护 ——
        self.emotion_est = EmotionEstimator(config.emotion)
        self.guardian = ScreenGuardian(config)
        self._last_care_t = 0.0        # 上次情绪关怀时刻（冷却，避免频繁安慰）
        self._care_since = None        # 负面情绪持续起点
        self._last_line_key = None     # (category, severity) 去重，避免逐帧刷台词
        self._last_line_t = 0.0
        self._last_review_t = time.time()
        self._calm_since = time.time()  # 持续"无异常"的起点，用于让文乃安静后打盹
        self._happy_until = 0.0         # HAPPY 表情的有效截止时刻（仅恢复良好时短暂触发）
        self._had_problem = False       # 上一阶段是否出现过不良用眼，用于触发"恢复→开心"

        if not force_simulate:
            self._try_real_backend(camera_index)

        # 台词扩充守护线程：LLM 可用时后台生成傲娇台词混入抽取池，
        # 让提醒/夸奖/关怀不再只有内置那几句；不可用时线程立即结束（零影响）。
        start_line_refresher(config)

    def set_mode(self, mode) -> None:
        """切换运行模式。支持传 Mode 或其字符串值。"""
        if not isinstance(mode, Mode):
            mode = Mode(mode)
        self.mode = mode
        if mode is Mode.REVIEW:
            self._last_review_t = time.time()  # 进入复盘从此刻起计时
        self._escalator = StrictEscalator()    # 切模式时重置升级计时

    def _try_real_backend(self, camera_index: int = 0) -> None:
        try:
            from ..vision.detectors import RealVisionBackend
            if not RealVisionBackend.available():
                self.fallback_reason = (
                    "未检测到 mediapipe / opencv 或模型文件，"
                    "请确认已安装依赖并下载 assets/models/*.task")
                return
            if self._use_process:
                # 建议3：摄像头采集+推理搬进独立子进程，UI 进程零推理负担
                from ..vision.worker import VisionWorker
                worker = VisionWorker(camera_index, self.t,
                                      self.config.fps_target)
                if worker.start():
                    self._worker = worker
                    self.backend_name = "摄像头实时检测 (MediaPipe · 独立进程)"
                else:
                    self.fallback_reason = worker.error or "视觉子进程启动失败"
                return
            import cv2
            cap = cv2.VideoCapture(camera_index)
            if not cap or not cap.isOpened():
                if cap:
                    cap.release()
                self.fallback_reason = f"无法打开摄像头(index={camera_index})"
                return
            self._real = RealVisionBackend()
            self._cap = cap
            self.backend_name = "摄像头实时检测 (MediaPipe)"
        except Exception as exc:
            self.fallback_reason = f"实时后端初始化异常: {exc}"
            self._real = None
            self._cap = None
            self._worker = None

    def _next_sample(self) -> FrameSample:
        now = time.time()
        if self._worker is not None:
            s = self._worker.latest()
            if s is not None:
                s.is_blink_event = self.blink.update(s.ear)
                self._last_ws = s
                return s
            if not self._worker.alive():
                # 子进程崩溃 → 永久回退模拟器（铁律：任何失败不影响演示）
                self.fallback_reason = "视觉子进程已退出，回退模拟数据"
                self.backend_name = "模拟数据"
                self._worker.close()
                self._worker = None
            elif self._last_ws is not None:
                # 子进程健在但暂无新帧（推理慢于 UI）→ 沿用上一帧，不重复计眨眼
                s = self._last_ws
                s.is_blink_event = False
                s.timestamp = now
                return s
            else:
                # 尚未收到首帧 → 先用模拟数据顶住
                s = self._sim.read(self.t, now)
                s.is_blink_event = self.blink.update(s.ear)
                return s
        if self._real and self._cap:
            ok, frame = self._cap.read()
            if ok:
                s = self._real.process(frame, self.t, now)
                s.is_blink_event = self.blink.update(s.ear)
                return s
        # 回退/模拟
        s = self._sim.read(self.t, now)
        s.is_blink_event = self.blink.update(s.ear)
        return s

    def tick(self) -> Snapshot:
        now = time.time()
        s = self._next_sample()
        # —— 游戏勿扰：前台全屏独占（竞技游戏/放映）→ 本帧自动免打扰 ——
        game = bool(self._fs_watch and self._fs_watch.is_fullscreen())
        self.game_mode = game
        # —— 表情情绪：由 blendshapes 估计并写回 sample，供指标累计情绪时间线 ——
        emotion = None
        if getattr(self.config.emotion, "enabled", True):
            emotion = self.emotion_est.estimate(s.blendshapes)
            s.emotion = emotion
        self.metrics.add(s, self.t)
        rest = self.rest.update(s.face_present, s.timestamp)

        blink_rate = self.metrics.blink_rate_realtime(
            self.t.blink_rate_window_sec, self.t.blink_rate_smooth)
        advices = self.advice.evaluate(
            blink_rate, self.metrics.avg_cva(), self.metrics.avg_tilt(),
            self.metrics.avg_distance(), rest,
        )
        next_break = max(
            0.0, self.t.break_interval_min * 60 - rest.since_last_break_sec)

        # —— 严格升级器：始终运行，得到逐级加深的严重度（驱动情绪/配色）——
        strict_alerts = self._escalator.update(advices, now)
        worst = self._escalator.worst_severity(strict_alerts)
        mood = Persona.mood_for(worst, s.face_present, healthy=not advices)
        line = self._persona_line(advices, worst, s.face_present, now)

        # —— 平静为基准，HAPPY 仅短暂出现 ——
        # 默认健康状态为 NORMAL(平静)；只有在"刚从不良用眼恢复"的瞬间，
        # 才让文乃开心几秒(HAPPY)，随后回到平静，避免一直咧嘴笑。
        healthy = (not advices) and s.face_present
        if advices:
            self._had_problem = True
        elif healthy and self._had_problem:
            self._happy_until = now + 6.0
            self._had_problem = False
        if mood == NORMAL and now < self._happy_until:
            mood = HAPPY

        # —— 安静足够久 → 文乃打盹（睡眠模式）——
        # 有异常或离座会重置"安静计时"；持续无异常超过阈值，文乃就犯困入睡、
        # 并停止说话，让睡眠形象真正出现（修掉"话太多导致从不睡"的问题）。
        if advices or not s.face_present:
            self._calm_since = now
        idle_sleep_sec = getattr(self.t, "idle_sleep_sec", 40.0)
        if (not advices) and s.face_present \
                and (now - self._calm_since) >= idle_sleep_sec:
            mood = SLEEPY
            line = None

        # —— 情绪关怀：检测到疲惫/压力/低落持续一段时间 → 文乃主动安慰 ——
        # （对齐商业计划书「心理健康呵护」；仅在有脸、非勿扰、非睡眠时触发，带冷却）
        care_line = self._emotion_care(emotion, s.face_present, mood, now)
        if care_line:
            line = care_line

        # —— 复盘模式：到点自动生成报告 ——
        auto_report = None
        if self.mode is Mode.REVIEW:
            interval = self.config.review_interval_min * 60
            if now - self._last_review_t >= interval:
                self._last_review_t = now
                try:
                    from .health_report import save_report
                    auto_report = save_report(self.metrics, self.config)
                except Exception:
                    auto_report = None

        # 勿扰模式 / 游戏勿扰：零弹窗零台词（仅后台记录数据）
        if self.mode is Mode.SILENT or game:
            line = None
            strict_alerts = []

        snap = Snapshot(
            backend=self.backend_name,
            face_present=s.face_present,
            blink_rate=blink_rate,
            blink_total=self.metrics.blink_count,
            cva=self.metrics.avg_cva(),
            distance=self.metrics.avg_distance(),
            screen_time_min=rest.screen_time_sec / 60.0,
            continuous_use_min=rest.continuous_use_sec / 60.0,
            next_break_in_sec=next_break,
            advices=advices,
            mode=self.mode.value,
            mood=mood,
            persona_line=line,
            worst_severity=worst,
            strict_alerts=strict_alerts,
            auto_report_path=auto_report,
            emotion=emotion,
            game_mode=game,
        )

        # —— 极端用眼守护：达阈值则产生干预指令（勿扰模式下也保护，安全优先；
        #     但游戏全屏时暂缓，避免团战关头弹遮罩，退出全屏恢复）——
        try:
            snap.guard_action = (None if game
                                 else self.guardian.evaluate(snap))
        except Exception:
            snap.guard_action = None
        return snap

    def _emotion_care(self, emotion, face_present, mood, now) -> Optional[str]:
        """负面情绪持续足够久且过冷却 → 返回一句关怀台词，否则 None。"""
        from .emotion import CARE_EMOTIONS
        cfg = self.config.emotion
        if (self.mode is Mode.SILENT or not face_present
                or mood == SLEEPY or emotion not in CARE_EMOTIONS
                or self.emotion_est.care_score() < cfg.care_min_score):
            self._care_since = None
            return None
        if self._care_since is None:
            self._care_since = now
            return None
        if (now - self._care_since) < cfg.care_sustain_sec:
            return None
        if (now - self._last_care_t) < cfg.care_cooldown_sec:
            return None
        self._last_care_t = now
        self._care_since = None
        return self.persona.care_for_emotion(emotion)

    def _persona_line(self, advices, worst, face_present, now) -> Optional[str]:
        """节流地产出一句角色台词；无新内容时返回 None（避免逐帧刷屏）。"""
        if self.mode is Mode.SILENT:
            return None
        if not face_present:
            key = ("sleepy", 0)
        elif advices:
            top = max(advices, key=lambda a: _SEV_RANK.get(a.level.value, 0))
            key = (top.category, worst)
        else:
            key = ("praise", 0)

        # 降低唠叨频率：同类问题至少隔 18s 再复述一次；状态良好时夸奖更克制(45s)。
        interval = 18.0 if advices else 45.0
        if key != self._last_line_key or (now - self._last_line_t) >= interval:
            self._last_line_key = key
            self._last_line_t = now
            if not face_present:
                return None  # 离座犯困：不唠叨
            if advices:
                top = max(advices, key=lambda a: _SEV_RANK.get(a.level.value, 0))
                return self.persona.line_for(top.category, worst)
            return self.persona.praise()
        return None

    def acknowledge_break(self) -> None:
        self.rest.acknowledge_break()

    def close(self) -> None:
        if self._worker:
            try:
                self._worker.close()
            except Exception:
                pass
            self._worker = None
        if self._cap:
            try:
                self._cap.release()
            except Exception:
                pass
        if self._real:
            try:
                self._real.close()
            except Exception:
                pass
