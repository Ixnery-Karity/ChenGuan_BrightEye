"""多端数据同步（建议5落地）：局域网轻量同步服务，纯标准库实现。

动机：用户离开电脑去玩手机，眼睛并没有休息——PC 与 Android 各自的
「用眼时长」必须合并，才能得出真实的全天候用眼负荷，大模型的行为
洞察也才准确。本模块在 PC 端起一个局域网 HTTP 服务（默认关闭，
--sync 或 config.sync.enabled 开启），手机端（同一 Wi-Fi）定期上报：

    POST /api/usage      Content-Type: application/json
    {"device": "android-xxx", "screen_time_min": 87.5,
     "blink_rate_avg": 12.3, "dominant_emotion": "tired"}   # 后两项可选

    GET  /api/summary    → PC 会话概览 + 各设备最新上报（JSON）
    GET  /api/ping       → {"app": "...", "version": "..."}  发现/联通性测试

安全与合规：
  · 仅监听局域网，可选共享口令（X-Sync-Token 头，config.sync.token）；
  · 只传输聚合指标（分钟数/频率/情绪标签），不传任何画面帧，隐私友好；
  · 上报按设备去重取最新、按自然日聚合，落盘 data/sync_devices.json。
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional


def _today() -> str:
    return time.strftime("%Y-%m-%d")


class SyncStore:
    """各设备最新上报的内存表 + JSON 落盘（按自然日聚合）。"""

    def __init__(self, data_dir: str = "data"):
        self._path = os.path.join(data_dir, "sync_devices.json")
        self._lock = threading.Lock()
        self._devices: Dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._devices = json.load(f)
        except Exception:
            self._devices = {}

    def _save(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._path), exist_ok=True)
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._devices, f, ensure_ascii=False, indent=1)
        except Exception:
            pass  # 落盘失败不影响内存态

    def update(self, device: str, payload: dict) -> None:
        with self._lock:
            self._devices[str(device)[:64]] = {
                "screen_time_min": max(0.0, float(payload.get("screen_time_min", 0.0))),
                "blink_rate_avg": payload.get("blink_rate_avg"),
                "dominant_emotion": payload.get("dominant_emotion"),
                "date": _today(),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
            self._save()

    def other_devices_today(self) -> Dict[str, float]:
        """今日各外部设备的用眼分钟数 {device: minutes}。"""
        with self._lock:
            return {d: r["screen_time_min"] for d, r in self._devices.items()
                    if r.get("date") == _today()}

    def total_other_min(self) -> float:
        return sum(self.other_devices_today().values())

    def snapshot(self) -> dict:
        with self._lock:
            return dict(self._devices)


class SyncServer:
    """局域网同步服务。start() 在守护线程中监听，任何异常静默降级。"""

    def __init__(self, config, monitor=None):
        cfg = getattr(config, "sync", None)
        self.port = getattr(cfg, "port", 8765)
        self.token = getattr(cfg, "token", "") or ""
        self.store = SyncStore(getattr(config, "data_dir", "data"))
        self.config = config
        self.monitor = monitor          # 可选：让 /api/summary 带 PC 实时数据
        self._httpd: Optional[ThreadingHTTPServer] = None
        self.error: Optional[str] = None

    def start(self) -> bool:
        server = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):   # 静默访问日志
                pass

            def _send(self, code: int, obj: dict) -> None:
                data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _auth_ok(self) -> bool:
                return (not server.token
                        or self.headers.get("X-Sync-Token", "") == server.token)

            def do_GET(self):
                if not self._auth_ok():
                    return self._send(401, {"error": "bad token"})
                if self.path.startswith("/api/ping"):
                    return self._send(200, {
                        "app": server.config.app_name,
                        "version": server.config.version})
                if self.path.startswith("/api/summary"):
                    body = {"devices": server.store.snapshot(),
                            "other_total_min": round(server.store.total_other_min(), 1)}
                    m = server.monitor
                    if m is not None:
                        body["pc"] = {
                            "screen_time_min": round(m.metrics.elapsed_min, 1),
                            "blink_rate_avg": round(m.metrics.blink_rate_avg(), 1),
                            "dominant_emotion": m.metrics.dominant_emotion(),
                        }
                    return self._send(200, body)
                return self._send(404, {"error": "not found"})

            def do_POST(self):
                if not self._auth_ok():
                    return self._send(401, {"error": "bad token"})
                if not self.path.startswith("/api/usage"):
                    return self._send(404, {"error": "not found"})
                try:
                    length = int(self.headers.get("Content-Length", 0))
                    payload = json.loads(self.rfile.read(length).decode("utf-8"))
                    device = payload.get("device") or self.client_address[0]
                    server.store.update(device, payload)
                    return self._send(200, {"ok": True,
                                            "other_total_min": round(server.store.total_other_min(), 1)})
                except Exception as exc:
                    return self._send(400, {"error": str(exc)})

        try:
            self._httpd = ThreadingHTTPServer(("0.0.0.0", self.port), _Handler)
        except OSError as exc:            # 端口被占等 → 降级不启用
            self.error = f"同步服务未启动: {exc}"
            return False
        threading.Thread(target=self._httpd.serve_forever,
                         daemon=True, name="brighteye-sync").start()
        return True

    def stop(self) -> None:
        if self._httpd:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            self._httpd = None
