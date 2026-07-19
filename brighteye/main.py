"""宸观 BrightEye 启动入口。

用法:
    python -m brighteye.main                 # 自动选择后端(有摄像头则实时检测)
    python -m brighteye.main --real          # 强制摄像头实时检测(失败即报错)
    python -m brighteye.main --camera 1      # 指定摄像头索引
    python -m brighteye.main --simulate      # 强制模拟数据(无摄像头演示)
    python -m brighteye.main --simulate --fast 8   # 加速8倍, 快速触发告警演示
    python -m brighteye.main --mode strict   # 指定启动模式(companion/strict/review/silent)
    python -m brighteye.main --pet           # 启动即收起为悬浮桌宠(弥悠)，仅留小窗陪伴
    python -m brighteye.main --mp-vision     # 视觉推理放独立子进程(性能隔离,失败自动回退)
    python -m brighteye.main --sync          # 开启局域网多端同步服务(手机上报用眼时长)
    python -m brighteye.main --headless 20   # 无界面, 跑20秒后直接出报告(自测/CI)
    python -m brighteye.main --report weekly   # 聚合历史生成周报(monthly=月报)
"""

from __future__ import annotations

import argparse
import sys
import time

from .config import CONFIG
from .core.monitor import Monitor

# Windows 控制台默认 GBK，统一切到 UTF-8 以正确输出中文报告
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


def _run_headless(monitor: Monitor, seconds: float) -> None:
    from .core.health_report import save_report
    end = time.time() + seconds
    while time.time() < end:
        monitor.tick()
        time.sleep(1.0 / CONFIG.fps_target)
    path = save_report(monitor.metrics, CONFIG)
    print(f"\n[报告已保存] {path}")
    monitor.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=f"{CONFIG.app_name} {CONFIG.subtitle}")
    parser.add_argument("--simulate", action="store_true", help="强制使用模拟数据")
    parser.add_argument("--real", action="store_true",
                        help="强制摄像头实时检测，启用失败则报错退出(不回退模拟)")
    parser.add_argument("--camera", type=int, default=0, help="摄像头索引(默认0)")
    parser.add_argument("--fast", type=float, default=1.0,
                        help="模拟时间加速倍数(默认1.0)")
    parser.add_argument("--seed", type=int, default=None, help="模拟随机种子")
    parser.add_argument("--headless", type=float, default=0.0,
                        help="无界面运行指定秒数后生成报告")
    parser.add_argument("--mode", type=str, default=None,
                        choices=["companion", "strict", "review", "silent"],
                        help="启动模式(默认取 config.default_mode)")
    parser.add_argument("--pet", action="store_true",
                        help="启动即收起为悬浮桌宠，仅留小窗陪伴")
    parser.add_argument("--mp-vision", action="store_true",
                        help="摄像头采集+推理放独立子进程(性能隔离)，失败自动回退")
    parser.add_argument("--sync", action="store_true",
                        help="开启局域网多端同步服务(默认端口 %d)" % CONFIG.sync.port)
    parser.add_argument("--report", type=str, default=None,
                        choices=["weekly", "monthly"],
                        help="基于历史数据直接生成周报/月报后退出(无需摄像头)")
    args = parser.parse_args()

    # —— 周报/月报：聚合 SQLite 历史直接出报告，不启动监测 ——
    if args.report:
        from .core.period_report import save_period_report
        save_period_report(args.report, CONFIG)
        return

    print(f"启动 {CONFIG.app_name} v{CONFIG.version} ...")
    monitor = Monitor(CONFIG, force_simulate=args.simulate,
                      sim_time_scale=args.fast, sim_seed=args.seed,
                      camera_index=args.camera, use_process=args.mp_vision)
    # 视觉后端在后台线程加载（UI 秒开）；--real / 真机 headless 需要确定结果再继续
    if args.real or (args.headless > 0 and not args.simulate):
        monitor.wait_backend()
    print(f"数据源：{monitor.backend_name}")
    if monitor.fallback_reason:
        print(f"[提示] 未启用实时检测，已回退模拟：{monitor.fallback_reason}")
        if args.real:
            print("[错误] --real 要求实时检测但启用失败，退出。")
            monitor.close()
            sys.exit(2)

    # —— 多端同步服务（建议5）：--sync 或配置开启；失败静默降级不阻塞 ——
    sync_server = None
    if args.sync or CONFIG.sync.enabled:
        CONFIG.sync.enabled = True   # 让报告端也读取同步数据（合并跨设备时长）
        from .core.sync import SyncServer
        sync_server = SyncServer(CONFIG, monitor)
        if sync_server.start():
            hint = "（需请求头 X-Sync-Token）" if CONFIG.sync.token else ""
            print(f"[同步] 局域网同步服务已启动: http://<本机IP>:{sync_server.port} "
                  f"POST /api/usage{hint}")
        else:
            print(f"[同步] {sync_server.error}")
            sync_server = None

    if args.mode:
        monitor.set_mode(args.mode)

    if args.headless > 0:
        try:
            _run_headless(monitor, args.headless)
        finally:
            if sync_server:
                sync_server.stop()
        return

    from .ui.app import DashboardApp
    app = DashboardApp(monitor)
    if args.pet:
        app._hide_dashboard()  # 启动即收起为悬浮桌宠
    try:
        app.run()
    finally:
        if sync_server:
            sync_server.stop()


if __name__ == "__main__":
    main()
