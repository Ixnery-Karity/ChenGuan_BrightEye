"""宸观 BrightEye —— 全局配置与健康阈值。

所有阈值均参考公开的眼科/职业健康常识设定，可在产品化阶段
按个体校准。阈值集中管理，便于演示时现场调参。
"""

from dataclasses import dataclass, field


@dataclass
class Thresholds:
    # 眨眼相关 ---------------------------------------------------------
    ear_blink: float = 0.21          # 眼纵横比(EAR)低于此值判定为闭眼
    ear_consec_frames: int = 2       # 连续低于阈值的帧数才计一次眨眼，过滤抖动
    blink_rate_low: float = 10.0     # 每分钟眨眼次数低于此值 → 干眼风险提示
    blink_rate_normal: float = 15.0  # 正常静息约15-20次/分钟
    blink_rate_window_sec: float = 30.0  # 实时眨眼频率的滚动统计窗口(秒)，窗口越短越灵敏
    blink_rate_smooth: float = 0.2       # 显示值的指数平滑系数(0~1)，抑制窗口边缘抖动

    # 摄像头标定 ------------------------------------------------------
    camera_hfov_deg: float = 60.0    # 假定的水平视场角(度)，用于由画面宽度推导焦距(分辨率无关)

    # 坐姿 / 体态 ------------------------------------------------------
    cva_good: float = 50.0           # 颅椎角(近似)大于此值为良好坐姿(度)
    cva_warning: float = 45.0        # 低于此值判定为明显前倾/低头
    cva_smooth: float = 0.25         # 颅椎角时间指数平滑系数(0~1)，抑制逐帧抖动；越小越稳
    cva_vis_floor: float = 0.30      # 单侧耳/肩可见度低于此值则该侧不参与CVA(抗遮挡/侧脸)
    shoulder_tilt_max: float = 8.0   # 双肩高度差角度，超过判定为高低肩(度)

    # 用眼距离 --------------------------------------------------------
    distance_min_cm: float = 45.0    # 建议用眼距离下限
    distance_ideal_cm: float = 55.0  # 推荐用眼距离

    # 时间管理(20-20-20 法则) ---------------------------------------
    continuous_use_warn_min: float = 45.0   # 连续用眼超过此分钟数 → 强提醒休息
    break_interval_min: float = 20.0        # 每用眼20分钟提示远眺
    break_look_far_sec: int = 20            # 远眺时长(秒)
    break_look_far_distance_m: int = 6      # 远眺距离(米)

    # 桌宠陪伴节奏 -----------------------------------------------------
    idle_sleep_sec: float = 40.0            # 持续无异常超过此秒数，文乃犯困入睡(降打扰)


@dataclass
class LLMConfig:
    """大模型接入配置。默认关闭自动探测也能跑（离线兜底）。

    后端优先级：显式 base_url / 环境变量(OpenAI 兼容) → 本地 Ollama → 无。
    任何后端不可用时，聊天走离线脚本、复盘走规则建议，绝不阻塞演示。
    """
    enabled: bool = True                 # 允许尝试探测大模型；False 则强制纯离线
    chat_model: str = "qwen2.5:7b-instruct"      # 桌宠聊天：指令跟随/角色扮演更自然
    analysis_model: str = "deepseek-r1:7b"        # 复盘分析：R1 蒸馏版推理能力强
    base_url: str = ""                   # OpenAI 兼容端点(留空则读环境变量/回落 Ollama)
    api_key_env: str = "BRIGHTEYE_LLM_KEY"        # 存放 API Key 的环境变量名
    ollama_host: str = "http://localhost:11434"
    timeout_sec: float = 20.0            # 单次请求超时；超时即安全回退离线
    chat_memory_turns: int = 6           # 桌宠多轮对话短期记忆保留的最近轮数


@dataclass
class GuardianConfig:
    """极端不正常用眼 → 强制干预（护眼守护）。

    多条件持续满足才触发，带冷却时间防误触；soft=全屏遮罩倒计时，
    hard=真系统锁屏(仅 Windows，其它平台自动降级 soft)。
    """
    enabled: bool = True
    mode: str = "soft"                   # soft(全屏遮罩+倒计时) | hard(系统锁屏)
    # —— 触发判据（需同时/持续满足，模拟"极端"用眼）——
    trigger_continuous_use_min: float = 90.0   # 连续用眼超过此分钟 → 计入极端
    trigger_distance_cm: float = 35.0          # 距离持续小于此值(过近)
    trigger_blink_rate: float = 6.0            # 眨眼率极低(远低于干眼阈)
    trigger_sustain_sec: float = 20.0          # 上述极端状态需持续的秒数
    cooldown_sec: float = 300.0                # 两次强制干预的最小间隔，防打扰
    force_rest_sec: int = 30                   # soft 遮罩强制休息倒计时(秒)


@dataclass
class EmotionConfig:
    """表情情绪分析（基于 MediaPipe blendshapes，零新增模型）。

    把 52 维 blendshape 归约为 FACS 动作单元(AU)，再按 Ekman 情绪原型加权打分，
    EMA 平滑 + 迟滞去抖，稳定可解释；持续负面情绪触发文乃关怀台词。
    """
    enabled: bool = True
    smooth: float = 0.25                 # AU 分数 EMA 平滑系数(0~1)，越小越稳
    # —— 关怀触发：负面/压力情绪持续多久后，文乃主动安慰 ——
    care_sustain_sec: float = 25.0
    care_cooldown_sec: float = 180.0     # 两次主动关怀的最小间隔
    # —— FACS-AU → Ekman 情绪原型打分参数 ——
    neutral_bias: float = 0.16           # 平静基线分：情绪原型需超过它才成立
    switch_margin: float = 0.10          # 迟滞裕度：新情绪需高出当前情绪此值才切换(去抖)
    care_min_score: float = 0.22         # 关怀触发所需的负面情绪最小置信分


@dataclass
class SystemConfig:
    """深度系统集成：游戏/专注识别 + 显示器物理亮度（DDC/CI）。

    game_mode：检测到前台程序全屏独占（竞技游戏/放映/专注写作）时，
    自动进入勿扰（不弹台词/弹窗/强制干预，数据仍后台记录），
    避免团战关头弹遮罩；退出全屏自动恢复。
    brightness：强制休息遮罩期间用 DDC/CI 调暗显示器物理亮度，
    需可选依赖 `pip install monitorcontrol`，缺失自动降级 no-op。
    """
    game_mode_enabled: bool = True
    game_poll_sec: float = 2.0           # 前台全屏检测节流间隔(秒)
    brightness_enabled: bool = False     # 默认关：装了 monitorcontrol 再开
    rest_dim_percent: int = 35           # 强制休息期间调暗到的亮度(%)


@dataclass
class SyncConfig:
    """多端数据同步（局域网 HTTP，纯标准库；默认关闭，--sync 开启）。

    手机端(同一 Wi-Fi) POST /api/usage 上报聚合用眼指标（只传分钟数/
    频率/情绪标签，不传画面，隐私友好），报告合并出全天候用眼负荷。
    """
    enabled: bool = False
    port: int = 8765
    token: str = ""                      # 非空则要求请求头 X-Sync-Token 一致


@dataclass
class AppConfig:
    app_name: str = "宸观 BrightEye"
    subtitle: str = "宸宇护目·智能护眼伴侣"
    version: str = "1.10.0-demo"
    thresholds: Thresholds = field(default_factory=Thresholds)
    llm: LLMConfig = field(default_factory=LLMConfig)
    guardian: GuardianConfig = field(default_factory=GuardianConfig)
    emotion: EmotionConfig = field(default_factory=EmotionConfig)
    system: SystemConfig = field(default_factory=SystemConfig)
    sync: SyncConfig = field(default_factory=SyncConfig)

    # 运行参数
    fps_target: int = 15             # 摄像头/模拟器目标帧率
    data_dir: str = "data"
    report_dir: str = "reports"

    # 默认运行模式（companion/strict/review/silent）
    default_mode: str = "companion"
    # 复盘模式：每多少分钟自动生成一次报告
    review_interval_min: float = 20.0

    # —— 电竞导播 / 二次元 UI 调色（降AI感：深黑底 + 高饱和撞色）——
    ui_bg: str = "#0A0E1A"           # 纯黑底
    ui_panel: str = "#121A2E"        # 卡片
    ui_panel2: str = "#1A2540"
    ui_teal: str = "#2EE6A6"         # 翠绿(护眼/瞳色) 主色
    ui_cyan: str = "#6FE7FF"         # 青 辅助
    ui_coral: str = "#FF5277"        # 缎带红 告警/强调
    ui_amber: str = "#FFC94D"        # 琥珀 次告警
    ui_fg: str = "#EAF0FF"
    ui_muted: str = "#8A93B5"

    # 粒子背景自适应降载：低配机上帧耗时超预算时自动减粒子数(不低于下限)
    ui_particle_count: int = 52
    ui_particle_min: int = 18

    # 免责声明(医疗合规：本产品只做行为提示，不做诊断与处方)
    disclaimer: str = (
        "本应用提供的健康建议与提示仅用于用眼/体态行为引导，"
        "不构成任何医疗诊断、治疗或用药处方。若眼部或颈肩持续不适，"
        "请及时前往正规医疗机构就诊。"
    )


CONFIG = AppConfig()
