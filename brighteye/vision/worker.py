"""视觉推理独立进程（性能隔离，建议3落地）。

动机：cv2 采集 + MediaPipe 推理若与 Tkinter UI 同进程，推理耗时波动
和 GIL 竞争会造成桌宠动画/粒子背景掉帧。本模块把「摄像头采集 + 推理」
整体搬进 multiprocessing 子进程，仅通过 Queue 把轻量的 FrameSample
（纯数值 dataclass，可 pickle）回传 UI 进程：

    UI/monitor 进程                 vision 子进程
    ┌───────────────┐   Queue    ┌──────────────────────┐
    │ tick() 取最新样本 │ ◄──────── │ 摄像头读帧 → MediaPipe │
    │ 眨眼/情绪/建议… │            │ 推理 → FrameSample    │
    └───────────────┘            └──────────────────────┘

设计约束（与项目铁律一致）：
  · 默认不启用（--mp-vision 开启），任何失败自动回退单进程/模拟器；
  · 子进程崩溃不影响 UI：latest() 拿不到新样本时上层沿用旧样本或回退；
  · Windows spawn 语义安全：worker 入口为模块级函数，参数全部可 pickle。
"""

from __future__ import annotations

import multiprocessing as mp
import queue as _queue
import time
from typing import Optional

from ..core.metrics import FrameSample

_READY_TIMEOUT_SEC = 15.0   # 等子进程完成模型加载+开摄像头的上限


def _worker_main(camera_index: int, thresholds, fps_target: int,
                 q: "mp.Queue", stop_evt: "mp.Event") -> None:
    """子进程入口：独占摄像头，持续推理并回传样本。"""
    try:
        import cv2
        from .detectors import RealVisionBackend
        if not RealVisionBackend.available():
            q.put(("status", "err:缺少 mediapipe/opencv 或模型文件"))
            return
        cap = cv2.VideoCapture(camera_index)
        if not cap or not cap.isOpened():
            q.put(("status", f"err:无法打开摄像头(index={camera_index})"))
            return
        backend = RealVisionBackend()
        q.put(("status", "ready"))
    except Exception as exc:  # 初始化失败 → 报告后退出，父进程回退
        try:
            q.put(("status", f"err:{exc}"))
        except Exception:
            pass
        return

    interval = 1.0 / max(1, fps_target)
    try:
        while not stop_evt.is_set():
            t0 = time.time()
            ok, frame = cap.read()
            if ok:
                try:
                    sample = backend.process(frame, thresholds, t0)
                except Exception:
                    sample = None
                if sample is not None:
                    # 队列满则丢最旧的：UI 永远消费"最新状态"，不排队积压
                    try:
                        q.put_nowait(("sample", sample))
                    except _queue.Full:
                        try:
                            q.get_nowait()
                        except _queue.Empty:
                            pass
                        try:
                            q.put_nowait(("sample", sample))
                        except _queue.Full:
                            pass
            # 推理耗时波动由子进程自己消化，不影响 UI 帧率
            time.sleep(max(0.0, interval - (time.time() - t0)))
    finally:
        try:
            cap.release()
        except Exception:
            pass
        try:
            backend.close()
        except Exception:
            pass


class VisionWorker:
    """父进程侧句柄：启动/轮询/关闭视觉子进程。"""

    def __init__(self, camera_index: int, thresholds, fps_target: int = 15):
        self._ctx = mp.get_context("spawn")   # Windows 语义，跨平台一致
        self._q = self._ctx.Queue(maxsize=4)
        self._stop = self._ctx.Event()
        self._proc = self._ctx.Process(
            target=_worker_main,
            args=(camera_index, thresholds, fps_target, self._q, self._stop),
            daemon=True, name="brighteye-vision")
        self.error: Optional[str] = None

    def start(self) -> bool:
        """启动子进程并等待就绪。失败返回 False（error 带原因）。"""
        self._proc.start()
        deadline = time.time() + _READY_TIMEOUT_SEC
        while time.time() < deadline:
            try:
                kind, payload = self._q.get(timeout=0.5)
            except _queue.Empty:
                if not self._proc.is_alive():
                    self.error = "视觉子进程意外退出"
                    return False
                continue
            if kind == "status":
                if payload == "ready":
                    return True
                self.error = str(payload)[4:] if str(payload).startswith("err:") else str(payload)
                return False
            # 就绪消息前不应有样本，忽略即可
        self.error = "视觉子进程启动超时"
        self.close()
        return False

    def latest(self) -> Optional[FrameSample]:
        """取队列中最新一帧样本；没有新样本返回 None（上层沿用旧值）。"""
        newest = None
        while True:
            try:
                kind, payload = self._q.get_nowait()
            except _queue.Empty:
                break
            if kind == "sample":
                newest = payload
        return newest

    def alive(self) -> bool:
        return self._proc.is_alive()

    def close(self) -> None:
        try:
            self._stop.set()
        except Exception:
            pass
        try:
            self._proc.join(timeout=2.0)
            if self._proc.is_alive():
                self._proc.terminate()
        except Exception:
            pass
