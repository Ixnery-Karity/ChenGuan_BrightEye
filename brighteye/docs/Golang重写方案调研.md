# Golang 重写方案调研（v1.13.0 · 仅方案，不整改）

> 目标：评估「用 Golang 重写以提升运行速度」的可行性、收益与代价，
> 并横向对比其他重写路线。**本文档只做技术决策依据，当前版本不动代码。**

---

## 一、先回答核心问题：慢在哪里？

重写前必须先定位瓶颈，否则「换语言」可能白忙。本项目的耗时分布：

| 环节 | 承担者 | 语言重写收益 |
| --- | --- | --- |
| 视觉推理（FaceLandmarker / PoseLandmarker） | **MediaPipe C++ 内核**（Python 只是壳） | ≈0（已是原生速度） |
| 摄像头采集/解码 | OpenCV C++ 内核 | ≈0 |
| 大模型推理 | Ollama（Go 写的独立进程） | ≈0（本就不在 Python 里） |
| UI 渲染（tkinter 粒子/画布） | Python 主线程 | **高**（这才是真瓶颈） |
| 业务编排（指标/建议/台词） | Python | 低（每帧微秒级） |
| 启动时间（导入 mediapipe/cv2 数秒） | Python import 机制 | **中高** |

**结论**：推理内核已是 C++/原生，换 Go 不会让"检测"变快；能变快的是
**UI 帧率、启动速度、内存占用、分发体积**。v1.9.0 的 `--mp-vision`
子进程隔离已解决大半 GIL 掉帧问题，因此重写属于「锦上添花的架构升级」，
不是「救命稻草」。

## 二、候选方案对比

### 方案 A：Go + Wails v2 UI，视觉留 Python 作 sidecar（推荐的 Go 路线）
- 架构：Go 主程序（Wails v2，前端 WebView2 渲染 UI）＋ Python 视觉子进程
  （复用现有 `vision/` 全部代码），进程间 stdio JSON 或 gRPC 传 `FrameSample`。
- 优点：UI 换成 Web 技术栈（动画/主题/图表全面升级）；Go 单二进制分发 ~10MB
  级；启动 <1s；视觉算法零重写、精度不回退；Ollama 调用天然顺手（同为 HTTP）。
- 缺点：双运行时（仍要带 Python + mediapipe，安装包瘦不下来）；跨进程调试成本；
  团队需新学 Go + 前端。
- 工作量估计：UI + 编排全重写，约 **4~6 周**（单人）。

### 方案 B：纯 Go（GoCV + ONNX Runtime）
- **MediaPipe 没有官方 Go 绑定**，纯 Go 必须换模型：GoCV（OpenCV 绑定）采集
  + ONNX Runtime Go 绑定跑 face/pose 模型（如 MediaPipe 模型转 ONNX 或换
  YuNet/RTMPose 等）。
- 优点：单语言单二进制，内存最低，工程最干净。
- 缺点：**blendshapes（52 维表情系数）没有现成 ONNX 替代**——情绪呵护卖点
  要重做；EAR/CVA/测距全链路精度需重新标定验证；CGO 交叉编译坑多。
- 工作量估计：**8~12 周**，且有精度回退风险。**不建议**。

### 方案 C：Rust + Tauri（与 A 同构的 Rust 路线）
- 与方案 A 架构相同（Tauri 壳 + Python sidecar），`docs/UI技术栈升级路线.md`
  已有铺垫。Rust 内存安全/性能略优，但学习曲线比 Go 陡；社区桌宠/托盘生态
  （tauri-plugin-positioner 等）成熟。
- 适合团队里有人愿意长期投入 Rust 时选择，否则 A 优先。

### 方案 D：不换语言——Python + Nuitka 编译（最低成本）
- Nuitka 把 Python 编译成 C 再编译原生 exe：实测同类项目**体积约 -43%、
  启动约 -34%**，import 阶段显著加速；代码零改动、离线铁律不破。
- 配合已有 `--mp-vision` 多进程与 PyInstaller 打包链路可平滑替换。
- 工作量估计：**2~4 天**（主要是打包脚本适配与回归测试）。

## 三、决策建议（分阶段）

| 阶段 | 动作 | 理由 |
| --- | --- | --- |
| 短期（比赛期） | **不重写**；可选做方案 D（Nuitka） | 演示稳定压倒一切，收益/风险比最高 |
| 中期（产品化） | 方案 A：Go+Wails UI + Python 视觉 sidecar | UI/分发/启动全面升级，视觉资产全保留 |
| 长期（视团队） | 视觉层评估 ONNX 化后再谈纯 Go/Rust | 等 blendshapes 有可靠替代再动推理层 |

> 一句话总结：**推理已经是 C++ 的速度，Go 换不来更快的检测；先用 Nuitka
> 拿下启动与体积，产品化阶段再用 Go+Wails 重做 UI 壳。**
