# 宸观 BrightEye · 更新日志（CHANGELOG）

> 版本号语义：`主.次.修订-demo`。每次发布新演示包都在此登记，并同步更新 `README.md`。
> 打包纪律：zip 带版本号，**保留旧包、不覆盖**。

---

## v1.13.0-demo — 2026-07-20

五件事：双击图标无窗口启动（VBS 启动器 + 桌面快捷方式，彻底告别命令行与
cmd 闪现）、Q 版弥悠软件图标全链路接入、启动加载页（Q 版弥悠 + 科技感
扫描环）、Golang 重写方案调研、天选姬桌宠技术栈调研（后两项仅出文档不整改）。

### 新增
- **双击无窗口启动**（任务3/4/7）：
  - `brighteye/启动宸观BrightEye.vbs`：双击即以 **pythonw**（GUI 版解释器，
    无控制台）启动软件，`WScript.Shell.Run ..., 0, False` 隐藏窗口不等待；
    pythonw 缺失自动回退 python，仍失败弹中文提示；UTF-16 编码保证中文注释
    不乱码；工作目录自动定位 challenge/。
  - `brighteye/创建桌面快捷方式.vbs`：双击在桌面生成「宸观 BrightEye」
    快捷方式（目标 wscript + 启动器，图标取 assets/app_icon.ico）。
  - **修复 cmd 弹窗闪现**（`core/llm_client.py`）：拉起 Ollama 的旧
    creationflags `DETACHED_PROCESS | CREATE_NO_WINDOW` 两标志同管控制台
    分配、按 Windows 文档互斥（行为未定义，正是黑窗闪现来源之一）——改为
    `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP` + `STARTUPINFO(SW_HIDE)`
    双保险；全项目 subprocess 仅此一处运行期调用，已全量排查。
  - `main.py` 在 pythonw 下安全：stdout/stderr 为 None 时 reconfigure 已被
    try 包住、print 为无害 no-op（CPython 行为），实测 pythonw 启动 UI 正常。
- **Q 版弥悠软件图标**（任务6）：用户提供的 Q 版弥悠立绘裁剪入库——
  `assets/app_icon.png`（512×512）+ `assets/app_icon.ico`（16~256 七尺寸）；
  仪表盘 `iconphoto(True, ...)` 接入标题栏/任务栏（Toplevel 同步继承），
  打包链路 `build_exe.py prepare_icon()`（v1.12.0 已就绪）自动生效。
- **启动加载页** `ui/splash.py`（任务5）：视觉后端后台加载的数秒里，全窗
  覆盖待机页——中央 Q 版弥悠头像（PNG 缺失走矢量猫耳兜底）+ 双层反向旋转
  扫描弧 + 呼吸光点绕环 + 「正在唤醒弥悠 ···」状态动效，固定星夜暗色配色；
  后端就绪（backend 不再含「加载中」）即收起（6 帧缩圈动画），最短展示
  1.2s 防闪过、25s 兜底超时绝不卡界面；主循环 30fps 驱动，零新增依赖。
- **Golang 重写方案调研** `docs/Golang重写方案调研.md`（任务1，仅方案）：
  先定位瓶颈——MediaPipe 推理已是 C++ 原生速度，换语言提速的是 UI/启动/
  体积而非检测；对比 Go+Wails+Python视觉sidecar（推荐的 Go 路线，4~6 周）/
  纯 Go GoCV+ONNX（blendshapes 无替代，不建议）/ Rust+Tauri / Python+Nuitka
  编译（体积-43% 启动-34%，2~4 天最低成本）；结论：比赛期不重写，可选
  Nuitka，产品化再上 Go+Wails。
- **天选姬桌宠技术栈调研** `docs/桌宠技术栈调研_天选姬.md`（任务2，仅调研）：
  天选姬 = **Unity + Live2D Cubism + Win32 三件套**（WS_EX_LAYERED 分层
  透明、LWA_COLORKEY/DWM 抠背景、全局鼠标钩子做像素级选择性点透、
  WS_EX_TOOLWINDOW+托盘）；横向对比 Unity(~800MB)/Electron+pixi-live2d-display
  (<100MB)/原生 C++(~50MB)/现状 tkinter；建议演示期保留 tkinter，产品化走
  Electron/Tauri + Live2D 独立桌宠进程（WebSocket 读监测数据，跨语言解耦）。

### 变更
- `ui/app.py`：新增窗口图标加载与 SplashOverlay 接入（`_loop` 驱动动画、
  `_refresh` 判定收起）；`config.py` 版本 1.12.0 → **1.13.0**。

---

## v1.12.0-demo — 2026-07-19

四件事：Ollama 随软件自动启动（大模型再不"失联"）、大模型一键安装脚本分发
（llm_models/）、仪表盘双主题可切换（弥悠·星夜 / 弥悠·奶糖）、封装打包接入软件图标。

### 新增
- **Ollama 自动启动**（`core/llm_client.py`）：软件启动探测不到 Ollama 服务时，
  自动定位可执行文件（PATH → `%LOCALAPPDATA%\Programs\Ollama`）并以**无窗口
  分离进程**拉起 `ollama serve`，轮询等待就绪（≤25s，均在后台线程不冻结 UI）；
  「无后端」结论 30s 后允许复检（覆盖慢启动/中途手动开启）；每进程只拉一次；
  `config.py` 新增 `llm.auto_start_ollama` 开关（默认开），聊天/复盘/周报/台词池
  四处调用点全部生效。实测：杀掉 Ollama 后启动软件，10.6s 自动拉起并成功对话。
- **大模型一键安装脚本** `llm_models/`：模型共 8.8GB 超 GitHub 各类上传限制
  （单文件 100MB / LFS 免费 1GB / Release 附件 2GB），仓库改放 KB 级分发脚本——
  `install_models.bat`（Windows：winget 装 Ollama + pull 两模型）与
  `install_models.sh`（Linux/macOS：官方脚本）+ README（含低配机小模型/云端 API 替代）。
- **仪表盘双主题系统** `ui/theme.py`：配色 token 集中管理，顶栏 🎨 一键切换——
  - 🌙 **弥悠·星夜**（默认）：午夜紫深底 + 发光描边 + 紫/青/粉高饱和撞色（科技感）；
  - 🍬 **弥悠·奶糖**：奶白粉紫浅底 + 糖果色卡片（可爱风，文字加深保对比度）；
  两套均按弥悠粉紫发/紫瞳定调；选择持久化 `data/ui_theme.json`；切换即时整体
  重建界面；强制休息遮罩固定暗色（休息时理应调暗，与主题无关）。
- **封装打包接入软件图标**（`tools/build_exe.py`）：图标放
  `brighteye/assets/app_icon.ico` 自动接入 exe（PyInstaller `--icon`）与
  Inno 安装向导（`SetupIconFile`）；只有 PNG 时用 Pillow 自动转多尺寸 .ico
  （16~256 全覆盖）；缺失/转换失败用默认图标不报错。
- **皮肤系统设计方案** `docs/皮肤系统设计方案.md`：界面直接上传立绘 + soul.md
  的皮肤包结构（目录即皮肤、全字段可缺省安全降级）、宽松 soul.md 解析约定、
  防出戏/医疗合规固定规则、两阶段实施拆分（本版仅出方案，下版实施）。

### 变更
- `ui/app.py`：配色由模块级常量改为实例级主题 token（`self.T`），docstring 换代；
  `config.py` 版本 1.11.0 → **1.12.0**。

---

## v1.11.0-demo — 2026-07-19

五件事：启动全面提速（UI 秒开 + 聊天不再冻结）、监测历史 SQLite 持久化、
周报/月报跨周期报告、桌宠人设升级为原创角色「弥悠」、好感度细分 + 分层记忆。

### 新增
- **历史持久化** `core/history.py`：SQLite 库 `data/history.db`（纯标准库 sqlite3，零新依赖），
  三表：`sessions`（每次会话结束自动入库 12 项指标）、`affection`（好感度状态）、
  `chat_events`（关键对话事件，保留最近 200 条）；全部操作 try/except 安全降级。
- **周报/月报** `core/period_report.py`：`--report weekly|monthly` 一条命令聚合历史出 HTML 报告——
  周期平均评分 + 四维雷达图（复用 report_charts）+ 每日用眼时长/评分趋势 SVG（自绘柱线双轴图）+
  情绪主基调 + **AI 跨周期行为洞察**（deepseek-r1 归因逐日波动，200 字内；不可用自动省略）。
- **桌宠人设升级：文乃 → 弥悠（原创角色，规避侵权）** `docs/弥悠人设.md`：
  宸观视觉引擎拟人化中枢；浅粉紫长发 + 猫耳 + 半睁紫瞳 + 黑色项圈（=本地隐私锁）+
  黑色桃心发夹（=分级关怀引擎）；「视觉负荷共鸣」（99% 算力盯用眼，她的困是替眼睛喊累）；
  拟声词「呜喵！/喵惹！」。`persona.py` 16 条分级台词 + 闲聊/夸奖/关怀全部按人设重写，
  `pet.py` 配色换粉紫发紫瞳，聊天人格提示词含身世/隐私锁/防出戏规则；
  新增 privacy 话题（项圈隐私锁台词）。立绘 PNG 文件名沿用 `wenna_*.png`（磁盘资产名不变）。
- **好感度细分**（`core/chat_engine.py`）：
  - 六档等级各配**语气指令**注入 LLM 系统提示词（陌生=疏离 → 深爱=直球），态度随好感进阶；
  - **单日正向增量上限 25**（防刷分）+ 同话题重复减半（原有）；
  - **日衰减**：离开 ≥2 天每天 -2，下限 10（不会掉回陌生）；
  - 好感度/累计轮数**跨会话持久化**（SQLite，启动自动恢复）。
- **分层记忆**：短期=会话内多轮 deque（原有）；**中期=SQLite 关键事件**
  （|Δ好感|≥3 的对话自动存档，下次聊天回注 LLM 上下文，弥悠「记得以前的事」）。

### 优化（启动提速）
- **视觉后端后台加载**（`core/monitor.py`）：mediapipe 导入 + 摄像头 + 双模型加载（共数秒）
  移入后台线程，**UI 秒开**、就绪后热切换到实时检测；`--real`/headless 仍等待确定结果。
- **聊天不再冻结**（`ui/chat.py`）：`respond()` 移入线程 + `after(0)` 回调，
  等待期显示「（想了想…）」动画；输入防重入。
- **LLM 预热**（`chat_engine.warm_up_async`）：启动后台发 1-token 请求，把 Ollama
  冷加载十几秒提前到空闲期，首条对话即快。
- **立绘模块级缓存**（`ui/pet.py`）：桌宠与聊天窗共用一份，floodfill 抠图只做一次。
- **台词扩充延迟启动**：LLM 台词池刷新线程延迟 20s（`llm.line_refresh_delay_sec`），错峰启动。

---

## v1.10.0-demo — 2026-07-17

三件事：健康报告可视化升级（雷达图/趋势图/热力图/风险标注）、大模型全面点亮
（Ollama 已装 qwen2.5:7b + deepseek-r1:7b，桌宠对话/报告洞察/提醒台词三处真跑 LLM）、
代码接入 git 并发布 GitHub 开源仓库。

### 新增
- **报告图表四件套** `core/report_charts.py`（纯标准库生成内联 SVG，零依赖）：
  - 🕸 **四维雷达图**：眨眼健康/用眼距离/坐姿体态/时长节律 分项百分制得分，综合评分居中；
  - 📈 **指标趋势图**：本次会话 眨眼率/距离/颅椎角 时间序列折线（10 秒采样）；
  - 🕒 **风险时段热力图**：24 小时用眼负荷条，红=不良用眼占比高；配文字标注
    （如「15:00-16:20 连续用眼 80 分钟无休息」「15 时段不良用眼占比 40%，为全天风险高峰」）；
  - 🎯 **针对性改善建议**：按最薄弱维度产出（如「眨眼频率偏低，建议有意识多眨眼」），
    规则版始终可用，与 AI 行为洞察互补。
- `core/metrics.py` 配套采集：趋势时间线(10s/点)、分小时用眼/不良负荷、连续用眼段
  （离席 >3 分钟封段），供上述图表与风险标注使用。
- **提醒台词接大模型** `core/persona.py`：后台守护线程用聊天模型按 4 场景×4 级严重度
  批量生成傲娇台词混入抽取池（另含夸奖/情绪关怀），台词不再单调重复；
  LLM 不可用时线程立即退出，行为与纯离线完全一致。
- **git 版本控制 + GitHub 开源**：仓库 `Ixnery-Karity/ChenGuan_BrightEye`，
  范围仅 brighteye 软件代码（.gitignore 排除商业计划书等竞赛文档、zip 演示包、运行时产物）。

### 大模型状态（本机已实测跑通）
- 本机已安装 Ollama 并 pull `qwen2.5:7b-instruct`（聊天/台词）与 `deepseek-r1:7b`（复盘洞察）；
- 三条链路端到端验证：桌宠多轮对话（is_llm=True）、报告「AI 行为洞察」段、动态台词池注入。

---

## v1.9.0-demo — 2026-07-16

响应导师五项工程化修改建议 + 合入 Gemini 交叉评审优化代码。所有新能力默认关闭或零依赖降级，离线可演示铁律不破。

### 新增（五项建议逐条落地）
- **建议③ 多进程架构** `vision/worker.py`：摄像头采集 + MediaPipe 推理整体搬进
  multiprocessing 子进程（spawn 语义，Windows 安全），仅经 Queue 回传轻量 `FrameSample`，
  规避 GIL 竞争导致的桌宠/粒子掉帧。`--mp-vision` 开启；子进程崩溃/启动失败自动回退单进程或模拟器。
- **建议④ 深度系统集成** `core/system_watch.py`：
  - `FullscreenWatcher`（纯 ctypes 零依赖）检测前台窗口全屏独占（竞技游戏/放映）→
    **游戏自动勿扰**：不弹台词/弹窗、暂缓强制干预，退出全屏自动恢复；仪表盘显示「🎮 已自动勿扰」；
  - `BrightnessController`（可选依赖 `monitorcontrol`）：强制休息遮罩期间经 DDC/CI
    调暗显示器物理亮度、结束恢复；未装库自动 no-op。
- **建议⑤ 多端数据同步** `core/sync.py`：纯标准库局域网 HTTP 服务（`--sync` 开启，默认端口 8765），
  手机端 `POST /api/usage` 上报聚合用眼指标（不传画面，隐私友好），支持可选口令 `X-Sync-Token`；
  健康报告与 AI 行为洞察自动合并**跨设备全天候用眼负荷**。接口文档见 `docs/多端数据同步API.md`。
- **建议② 一键安装包** `tools/build_exe.py`：PyInstaller onedir 一键打包（自动收
  mediapipe/cv2/模型资产）+ 自动生成 Inno Setup 中文安装向导脚本；指南见 `docs/打包发布指南_一键安装包.md`。
- **建议① UI 框架**：演示期保留 Tkinter（离线铁律），落地**粒子背景自适应降载**
  （`ui/particles.py` 帧耗时 EMA 超预算自动减粒子、空闲恢复，粒子数进 config）；
  PySide6/Tauri 迁移路线文档见 `docs/UI技术栈升级路线.md`。

### 合入 Gemini 交叉评审代码（`gemini_code_src/`）
- **坐姿 CVA 换同侧拓扑算法** `vision/detectors.py`：左耳配左肩/右耳配右肩分别计算再按可见度加权，
  替代旧「双肩中点近似 C7」法，躯干侧转/侧脸时更稳（保留原 EMA 平滑与可见度门限）。
- **情绪判定新增优先级仲裁** `core/emotion.py`：打分接近时按 疲惫>压力>低落>积极 仲裁，
  优先识别更需要关怀的状态；Gemini 的"blendshape 直接映射"方案**未采纳**（会丢失 FACS-AU 可解释性卖点）。

### 变更
- `core/monitor.py`：`Monitor(use_process=...)` 串接子进程后端；`Snapshot` 增 `game_mode` 字段；
  游戏勿扰帧静默台词/告警并暂缓 guardian。
- `main.py`：新增 `--mp-vision`、`--sync` 参数；`config.py` 新增 `SystemConfig`/`SyncConfig`，版本 1.8.2 → **1.9.0**。

---

## v1.8.2-demo — 2026-07-14

品牌更名：中文名由「明眸」正式更名为「**宸观**」，英文名 `BrightEye` 保留不变。

### 变更
- **项目全名统一为**「宸观 BrightEye —— 宸宇护目，智能时长管控护眼伴侣系统」；
  `config.py` `app_name` 改为 `宸观 BrightEye`，副标题由「AI 视觉健康管家」改为「**宸宇护目·智能护眼伴侣**」。
- **全仓文本全量替换** `明眸 → 宸观`（含代码注释、README、CHANGELOG、商业计划书/定价/金融量化/路演等各类文档、
  Android `strings.xml`、历史 HTML 报告）；团队名「明眸科技团队」同步更名为「宸观科技团队」。
- **重命名 11 个含「明眸」的 `.md` 文档**（商业计划书、路演大纲、招募说明、定价方案、金融量化报告等）为「宸观」。
- 版本号 1.8.1 → **1.8.2**。

### 未处理（后续按需）
- `.docx / .pptx` 二进制文档（商业计划书 Word/PPT 正本）内部文字与旧版 `*.zip` 演示包**未改动**；
  沿用打包纪律「保留旧包不覆盖」，后续正式发版再依据源码重新导出/打包。

---

## v1.8.1-demo — 2026-07-05

聚焦「表情情绪判定更准」与「大模型真正跑通」两件事，仍严守离线优先铁律。

### 变更
- **表情情绪引擎升级为可解释三级管线** `core/emotion.py`：由早期单阈值判定，改为
  **blendshapes → FACS 动作单元(AU) → Ekman 情绪原型加权打分 → EMA 平滑 + 迟滞(hysteresis)去抖**；
  用多 AU 组合区分「压力(皱眉+抿唇+眼睑收紧)」「疲惫(眨眼加重+哈欠)」「低落(内眉上扬+嘴角下拉)」，
  显著降低误判，全程零训练、可逐条溯源到肌肉动作（适合答辩讲解）。
- `config.py` `EmotionConfig` 参数换代：以 `neutral_bias`（平静基线）/`switch_margin`（迟滞裕度）/
  `care_min_score`（关怀置信门限）替换旧的单表情阈值。
- `core/monitor.py` 情绪关怀触发新增**置信门限**：负面情绪不仅要持续，还需打分足够高才让文乃主动安慰，进一步防误触。
- `core/simulator.py` `_fake_blendshapes` 扩充为覆盖新 AU 通道（眨眼/内眉/抿唇/脸颊等），
  无摄像头演示可稳定复现 积极→疲惫→压力 的情绪演化。
- `core/health_report.py` 复盘洞察请求超时下限放宽至 90s，容忍 DeepSeek-R1 首帧冷加载（报告非实时、可等待）。

### 大模型（已本地跑通验证）
- 本机 Ollama 已拉取 `qwen2.5:7b-instruct`（聊天）与 `deepseek-r1:7b`（复盘分析）并**端到端验证**：
  桌宠聊天 `is_llm=True` 自然多轮、注入用眼/情绪上下文；复盘报告正常渲染「🧠 AI 行为洞察」段落（思维链已剥离）。
- `docs/大模型接入与部署指南.md` 第六节补充 **FACS-AU 引擎原理** 与**可选 ML 训练升级路径**
  （FER2013 + blendshapes 特征 + sklearn/SVM，预留 `emotion_clf.pkl` 软加载设计，当前未接线以保持零依赖）。

---

## v1.8.0-demo — 2026-07-05

三项 PC 端桌宠增强，全部遵循「离线优先，任意机器可演示」铁律（大模型不可用时自动回退）。

### 新增
- **统一大模型客户端** `core/llm_client.py`：唯一 LLM 入口，仅用标准库 `urllib`（零重依赖）；
  自动探测后端优先级 ①OpenAI 兼容 API（含 DeepSeek 官方，读环境变量）→ ②本地 Ollama → ③离线兜底；
  内置 `strip_think()` 剥离 DeepSeek-R1 的 `<think>` 思维链；任何异常/超时安全返回 `None`。
- **桌宠对话接入大模型** `core/chat_engine.py`：`_try_llm()` 真正实现，多轮对话短期记忆（`deque`）+
  用眼/情绪上下文注入；好感度增量仍由离线规则判定（防刷分）；失败回退傲娇脚本。默认聊天模型 `qwen2.5:7b-instruct`。
- **复盘报告 AI 行为洞察** `core/health_report.py`：`llm_insight()` 用复盘模型（默认 `deepseek-r1:7b`）
  基于结构化指标产出「行为习惯洞察 + 个性化建议」，文本/HTML 报告新增「🧠 AI 行为洞察」段落；不可用时降级为规则建议。
- **表情情绪分析** `core/emotion.py`：复用 MediaPipe FaceLandmarker 的 52 维 blendshapes（零新增模型），
  **FACS 动作单元(AU) → Ekman 情绪原型** 的可解释映射 + EMA 平滑 + 迟滞去抖；输出 积极/平静/疲惫/压力/低落。
- **极端用眼强制干预** `core/guardian.py`：连续用眼久 + 距离持续过近 + 眨眼率极低 同时持续满足才触发（带冷却）；
  soft=全屏遮罩+强制休息倒计时（默认），hard=Windows 系统锁屏（非 Windows 自动降级 soft）。
- **情绪关怀台词** `core/persona.py`：检测到疲惫/压力/低落持续一段时间，文乃以聊天形式主动安慰（对齐商业计划书心理呵护）。
- **部署指南** `docs/大模型接入与部署指南.md`：本地 Ollama / 云端 API / 离线兜底三方案对比与配置步骤。

### 变更
- `config.py` 新增 `LLMConfig` / `GuardianConfig` / `EmotionConfig` 三段配置；版本号 1.7.0 → **1.8.0**。
- `vision/detectors.py` FaceLandmarker 开启 `output_face_blendshapes=True`，`FrameSample` 新增 `blendshapes`/`emotion` 字段。
- `core/metrics.py` 累计情绪时间线（`emotion_seconds`）并提供 `dominant_emotion()` / `emotion_distribution()`。
- `core/monitor.py` `Snapshot` 新增 `emotion` / `guard_action`；串接情绪估计与守护判定。
- `core/simulator.py` 合成随用眼时长渐趋疲惫的表情系数，使无摄像头演示也能展示情绪与关怀触发。
- `ui/app.py` 接入 soft 强制遮罩/hard 锁屏，并把实时用眼/情绪状态注入聊天引擎。
- `requirements.txt` 说明 LLM 为可选、零新增 pip 依赖（仅需 Ollama 或 API 环境变量）。

### 依赖
- 无新增 pip 依赖。启用大模型二选一：本地 `ollama pull qwen2.5:7b-instruct` + `deepseek-r1:7b`，或设 `BRIGHTEYE_LLM_BASE`/`BRIGHTEYE_LLM_KEY`。

---

## v1.7.0-demo 及更早

早于本更新日志建立，历史演示包见项目根目录 `宸观BrightEye_可演示版_v1.7.0-demo.zip` 等：
- 悬浮桌宠「文乃」（程序化矢量形象、傲娇人设、可拖拽置顶）；
- 四运行模式（陪伴 / 严格 / 复盘 / 勿扰）与严格模式逐级升级（颜色加深）；
- 电竞导播风 UI + 粒子背景；眨眼(EAR)/距离(瞳距)/坐姿(CVA·高低肩)监测；
- 20-20-20 计时、分级健康建议、综合评分与 HTML 报告；Android 版原型。
