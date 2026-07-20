"""核心编排器：把视觉后端、指标统计、计时、建议串成一条流水线。

monitor 与 UI 解耦：UI 只需周期性调用 `tick()` 拿到最新快照，
或注册回调。后端选择策略：
  1. 优先真实摄像头 (RealVisionBackend, 需 mediapipe+opencv+摄像头)
  2. 回退到模拟器 (SimulatedVisionBackend)，保证任意环境可演示
"""

from __future__ import annotations

import threading
import time
from collections import deque
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


class _CameraSampler:
    """独立采样线程：以固定节奏做 摄像头采集 + MediaPipe 推理 + 眨眼判定。

    把耗时的 read/推理从 UI 主线程剥离：
      - UI 卡顿不再拖慢采样节奏 → 快速眨眼不漏检、指标帧率稳定；
      - 推理耗时不再阻塞 UI → 粒子/桌宠不掉帧；
      - 样本入有界队列，tick() 批量取走，一帧不丢（眨眼事件不吞）。
    """

    def __init__(self, cap, backend, blink: BlinkCounter, thresholds, fps: int):
        self._cap = cap
        self._backend = backend
        self._blink = blink
        self._t = thresholds
        self._interval = 1.0 / max(1, fps)
        self._q: deque = deque(maxlen=120)
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.error: Optional[str] = None
        self._thread = threading.Thread(
            target=self._run, daemon=True, name="camera-sampler")

    def start(self) -> None:
        self._thread.start()

    def alive(self) -> bool:
        return self._thread.is_alive()

    def drain(self) -> List[FrameSample]:
        """取走队列中的全部样本（调用方逐个喂给 metrics，不丢眨眼事件）。"""
        with self._lock:
            out = list(self._q)
            self._q.clear()
        return out

    def close(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2.0)

    def _run(self) -> None:
        fail = 0
        while not self._stop.is_set():
            t0 = time.time()
            try:
                ok, frame = self._cap.read()
                if ok:
                    fail = 0
                    s = self._backend.process(frame, self._t, time.time())
                    s.is_blink_event = self._blink.update(s.ear, s.timestamp)
                    with self._lock:
                        self._q.append(s)
                else:
                    fail += 1
                    if fail >= 30:      # 摄像头持续读不到帧 → 线程退出，上层回退
                        self.error = "摄像头连续读帧失败"
                        return
            except Exception as exc:
                self.error = f"采样线程异常: {exc}"
                return
            # 固定节奏：扣除本帧耗时后补眠，保证按 fps_target 稳定采样
            delay = self._interval - (time.time() - t0)
            if delay > 0:
                self._stop.wait(delay)


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
        self.blink = BlinkCounter(self.t.ear_blink, self.t.ear_consec_frames,
                                  getattr(self.t, "ear_min_close_sec", 0.10))

        self.backend_name = "模拟数据"
        self.fallback_reason = None  # 实时后端失败时记录原因，供上层提示
        self._real = None
        self._cap = None
        self._sampler: Optional[_CameraSampler] = None  # 独立采样线程（单进程实时模式）
        self._last_rt = None           # 采样线程暂无新帧时沿用的上一帧
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
        self._calm_since = time.time()  # 持续"无异常"的起点，用于让弥悠安静后打盹
        self._happy_until = 0.0         # HAPPY 表情的有效截止时刻（仅恢复良好时短暂触发）
        self._had_problem = False       # 上一阶段是否出现过不良用眼，用于触发"恢复→开心"

        # —— 启动优化：视觉后端（mediapipe 导入/摄像头/模型加载共需数秒）
        # 搬到后台线程，UI 先用模拟数据秒开窗口，就绪后自动热切换真实检测。
        self._backend_thread: Optional[threading.Thread] = None
        if not force_simulate:
            self.backend_name = "模拟数据（视觉后端加载中…）"
            self._backend_thread = threading.Thread(
                target=self._load_backend_bg, args=(camera_index,),
                daemon=True, name="vision-loader")
            self._backend_thread.start()

        # 台词扩充守护线程：LLM 可用时后台生成傲娇台词混入抽取池。
        # 延迟启动，避免开机即批量占用 Ollama、拖慢首条真实对话。
        delay = getattr(getattr(config, "llm", None), "line_refresh_delay_sec", 20.0)
        _t = threading.Timer(max(0.0, delay), start_line_refresher, args=(config,))
        _t.daemon = True
        _t.start()

    def _load_backend_bg(self, camera_index: int) -> None:
        """后台线程加载真实视觉后端；失败时把名称复位为纯模拟数据。"""
        try:
            self._try_real_backend(camera_index)
        finally:
            if not (self._real or self._worker):
                self.backend_name = "模拟数据"

    def wait_backend(self, timeout: Optional[float] = None) -> None:
        """等待视觉后端加载完成（--real / headless 真机模式需要确定结果）。"""
        if self._backend_thread is not None:
            self._backend_thread.join(timeout)

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
            try:
                # 缓冲区压到 1 帧：读取慢于相机帧率时拿到的仍是最新帧，
                # 消除 0.3s+ 的画面滞后（部分驱动不支持则忽略）。
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            except Exception:
                pass
            self._real = RealVisionBackend()
            self._cap = cap
            # 独立采样线程：采集+推理+眨眼判定按 fps_target 固定节奏运行，
            # 与 UI 主循环解耦（UI 卡顿不再造成漏检，推理不再拖累 UI）。
            self._sampler = _CameraSampler(
                cap, self._real, self.blink, self.t, self.config.fps_target)
            self._sampler.start()
            self.backend_name = "摄像头实时检测 (MediaPipe · 采样线程)"
        except Exception as exc:
            self.fallback_reason = f"实时后端初始化异常: {exc}"
            self._real = None
            self._cap = None
            self._worker = None

    def _next_samples(self) -> List[FrameSample]:
        """取本轮全部新样本（至少 1 个；最后一个代表"当前"状态）。

        - 采样线程/子进程模式：批量取走队列样本，眨眼事件一帧不丢；
        - 无新帧时沿用上一帧（不重复计眨眼）；后端崩溃自动回退模拟器。
        """
        now = time.time()
        if self._worker is not None:
            s = self._worker.latest()
            if s is not None:
                s.is_blink_event = self.blink.update(s.ear, s.timestamp)
                self._last_ws = s
                return [s]
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
                return [s]
            else:
                # 尚未收到首帧 → 先用模拟数据顶住
                s = self._sim.read(self.t, now)
                s.is_blink_event = self.blink.update(s.ear, now)
                return [s]
        if self._sampler is not None:
            batch = self._sampler.drain()
            if batch:
                self._last_rt = batch[-1]
                return batch
            if not self._sampler.alive():
                # 采样线程退出（摄像头拔出等）→ 回退模拟器
                self.fallback_reason = self._sampler.error or "采样线程已退出"
                self.backend_name = "模拟数据"
                self._sampler = None
            elif self._last_rt is not None:
                # 线程健在但本轮暂无新帧 → 沿用上一帧（不重复计眨眼）
                s = self._last_rt
                s.is_blink_event = False
                s.timestamp = now
                return [s]
            else:
                s = self._sim.read(self.t, now)
                s.is_blink_event = self.blink.update(s.ear, now)
                return [s]
        # 回退/模拟
        s = self._sim.read(self.t, now)
        s.is_blink_event = self.blink.update(s.ear, now)
        return [s]

    def tick(self) -> Snapshot:
        now = time.time()
        batch = self._next_samples()
        s = batch[-1]                     # 最后一帧代表"当前"状态
        # —— 游戏勿扰：前台全屏独占（竞技游戏/放映）→ 本帧自动免打扰 ——
        game = bool(self._fs_watch and self._fs_watch.is_fullscreen())
        self.game_mode = game
        # —— 表情情绪：由 blendshapes 估计并写回 sample，供指标累计情绪时间线 ——
        emotion = None
        if getattr(self.config.emotion, "enabled", True):
            emotion = self.emotion_est.estimate(s.blendshapes)
            s.emotion = emotion
        # 批量入指标：采样线程积压的每一帧都计入（眨眼计数一帧不丢）
        for sample in batch:
            self.metrics.add(sample, self.t)
        rest = self.rest.update(s.face_present, s.timestamp)

        # 实时值：距离/颅椎角用短窗（默认 3s）墙钟均值，姿势变化秒级上屏；
        # 会话级 avg_* 仅供报告使用。
        rt_win = getattr(self.t, "realtime_window_sec", 3.0)
        cur_cva = self.metrics.recent_cva(rt_win)
        cur_tilt = self.metrics.recent_tilt(rt_win)
        cur_dist = self.metrics.recent_distance(rt_win)

        blink_rate = self.metrics.blink_rate_realtime(
            self.t.blink_rate_window_sec, self.t.blink_rate_smooth)
        advices = self.advice.evaluate(
            blink_rate, cur_cva, cur_tilt, cur_dist, rest,
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
        # 才让弥悠开心几秒(HAPPY)，随后回到平静，避免一直咧嘴笑。
        healthy = (not advices) and s.face_present
        if advices:
            self._had_problem = True
        elif healthy and self._had_problem:
            self._happy_until = now + 6.0
            self._had_problem = False
        if mood == NORMAL and now < self._happy_until:
            mood = HAPPY

        # —— 安静足够久 → 弥悠打盹（睡眠模式）——
        # 有异常或离座会重置"安静计时"；持续无异常超过阈值，弥悠就犯困入睡、
        # 并停止说话，让睡眠形象真正出现（修掉"话太多导致从不睡"的问题）。
        if advices or not s.face_present:
            self._calm_since = now
        idle_sleep_sec = getattr(self.t, "idle_sleep_sec", 40.0)
        if (not advices) and s.face_present \
                and (now - self._calm_since) >= idle_sleep_sec:
            mood = SLEEPY
            line = None

        # —— 情绪关怀：检测到疲惫/压力/低落持续一段时间 → 弥悠主动安慰 ——
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
            cva=cur_cva,
            distance=cur_dist,
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
        if self._backend_thread is not None and self._backend_thread.is_alive():
            self._backend_thread.join(timeout=3.0)   # 等加载收尾，避免释放竞态
        if self._worker:
            try:
                self._worker.close()
            except Exception:
                pass
            self._worker = None
        if self._sampler:
            try:
                self._sampler.close()   # 先停采样线程，再释放摄像头（避免竞态）
            except Exception:
                pass
            self._sampler = None
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
