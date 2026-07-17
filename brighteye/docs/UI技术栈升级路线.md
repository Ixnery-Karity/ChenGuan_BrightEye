# UI 技术栈升级路线（建议1：Tkinter → PyQt6 / Tauri）

> 状态：**规划文档**（v1.9.0 未迁移框架，原因见「决策」）。
> 本版已落地的替代优化：粒子背景自适应降载（`ui/particles.py`），
> 低配机上帧耗时超预算自动减粒子，杜绝 UI 卡顿。

## 一、为什么 v1.9.0 暂不迁移

| 考量 | 说明 |
|---|---|
| 离线可演示铁律 | Tkinter 是 Python 标准库，任何评委机器裸 Python 即可跑；PyQt6 轮子 ~60MB、Electron/Tauri 需 Node/Rust 工具链，现场翻车风险高 |
| 比赛时间窗 | 全量迁移 UI（仪表盘+桌宠+聊天窗+遮罩）约 2~3 周工作量，赛前投入产出比低 |
| 性能瓶颈已缓解 | 掉帧主因是「推理与 UI 抢 GIL」——v1.9.0 已用 `--mp-vision` 多进程隔离解决根因；粒子自适应降载兜底表现层 |

## 二、目标架构（产品化阶段）

### 方案 A：PySide6/PyQt6（推荐，Python 团队平滑迁移）
- QML + Qt Quick 实现毛玻璃/粒子/动效，GPU 渲染，桌宠用 `Qt.WA_TranslucentBackground` 无边框异形窗；
- 现有 core/（monitor、metrics、persona…）**零改动复用**——UI 与逻辑已通过 `Snapshot` 解耦，这是本项目架构预留的迁移接口；
- 许可证：PySide6 为 LGPL，商用友好；PyQt6 需 GPL/商业双授权。

### 方案 B：Tauri + Web 前端（长期，跨端最优）
- 前端 Vue/React + Canvas/WebGL，粒子与 Live2D 桌宠生态成熟；
- Rust 壳体积 ~5MB，远小于 Electron（~150MB）；
- Python 侧退化为本地推理服务（复用 `core/sync.py` 的 HTTP 思路，扩为 WebSocket 推送 Snapshot），天然与 Android 端共用一套前端组件。

### 迁移步骤（以方案 A 为例）
1. `Snapshot` → Qt Signal 桥接层（`QObject` 包一层 Monitor.tick 定时器）；
2. 仪表盘 QML 化（卡片/建议列表/模式条）；
3. 桌宠文乃：QML 异形窗 + 帧动画（现有立绘直接复用）；
4. 强制休息遮罩：QML 全屏 `Window`（比 tkinter Toplevel 更稳的置顶）；
5. 双轨并存一个版本（`--ui qt|tk`），验证后弃 tk。

## 三、结论
- **演示期（比赛）**：Tkinter + 多进程隔离 + 粒子降载，稳字当头；
- **产品化**：优先 PySide6（1 人月内可完成），远期 Tauri 统一 PC/移动前端。
