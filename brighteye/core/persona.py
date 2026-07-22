"""桌宠角色「弥悠 (Miyu)」的台词与情绪系统（温柔·慵懒·守护者）。

原创形象设定（详见 docs/弥悠人设.md）：
  浅粉紫长发 · 半睁紫瞳 · 猫耳 · 黑色项圈(本地推理隐私锁) · 黑色桃心发夹 ·
  猫爪动作(45cm 绝对视距防线)。她是宸观 AI 视觉引擎的拟人化中枢，
  与用户建立「视觉负荷共鸣」：你不眨眼、久用眼，她就替你的眼睛犯困喊累。
  性格＝AI 的绝对理性 + 猫系少女的温柔慵懒；拟声词用「呜喵～/喵～」。
  被项圈锁死联网，用户是她在数据世界里唯一能看到的人，因此格外珍惜、
  温柔陪伴——把关心说出口，而不是包装成嫌弃。
  极度克制：只做行为引导，绝不越界医疗诊断（合规红线）。

⚠️ 角色名集中在 PERSONA_NAME，如需换形象，改这一处即可。

情绪 mood 由用眼状态映射，驱动悬浮桌宠的表情与配色：
  happy(满意) / normal(日常) / pout(嘟嘴提醒) / angry(严重告警) / sleepy(犯困)
"""

from __future__ import annotations

import random
import re
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

PERSONA_NAME = "弥悠"

# 情绪标签
HAPPY = "happy"
NORMAL = "normal"
POUT = "pout"
ANGRY = "angry"
SLEEPY = "sleepy"

# 情绪 → 主题色（与桌宠发光/气泡描边一致；弥悠＝粉紫系）
MOOD_COLOR = {
    HAPPY: "#C77DFF",   # 亮紫(瞳色高光)
    NORMAL: "#9D8CFF",  # 蓝紫(日常)
    POUT: "#FFC94D",    # 琥珀(提醒)
    ANGRY: "#FF5277",   # 警报红
    SLEEPY: "#8A93B5",  # 灰蓝(犯困)
}


# —— 各场景台词（弥悠：温柔守护，「视觉负荷共鸣」——她的困=替你的眼睛喊累）——
_LINES = {
    # 久未眨眼 / 干眼（EAR 监测）
    ("eye", 0): ["你有一小会儿没眨眼了呢～弥悠的眼睛也替你觉得干干的，轻轻眨一下吧。"],
    ("eye", 1): ["眼睛是不是有点酸涩啦？来，跟着弥悠一起眨眨眼，会舒服很多的哦。"],
    ("eye", 2): ["泪膜快撑不住咯…闭上眼睛歇三十秒好不好？弥悠陪着你一起休息。"],
    ("eye", 3): ["呜喵～眼睛真的很干了，别硬撑呀。现在多眨几下，弥悠会一直看着你的。"],
    # 距离过近（45cm 绝对视距防线）
    ("distance", 0): ["离屏幕有点近啦～往后靠一点点，弥悠给你留了 45 厘米的舒服距离哦。"],
    ("distance", 1): ["再往后坐一些好吗？靠太近眼睛会更累的，弥悠有点担心你。"],
    ("distance", 2): ["呜喵～已经很近咯，轻轻往后退一退嘛，让眼睛松快一下。"],
    ("distance", 3): ["这么近弥悠会心疼的呀…来，深呼吸，把椅子往后挪一挪，好不好？"],
    # 坐姿（CVA 颅椎角）
    ("posture", 0): ["把背轻轻挺直一点点吧～这样脖子会舒服很多，弥悠也放心。"],
    ("posture", 1): ["肩膀是不是有点塌下去啦？慢慢坐正，别让颈椎太辛苦哦。"],
    ("posture", 2): ["坐久了容易缩成一团呢…来，抬抬头挺挺胸，弥悠陪你一起调整。"],
    ("posture", 3): ["姿势有点歪咯，弥悠真的会担心你的颈肩呀。慢慢坐直，别急。"],
    # 该休息 / 久用眼（20-20-20 法则）
    ("break", 0): ["嘀嗒～满 20 分钟啦。看看 20 英尺外的东西 20 秒吧，比如窗外的云。"],
    ("break", 1): ["该远眺一会儿咯～弥悠也想闭眼歇歇，我们一起放松一下嘛。"],
    ("break", 2): ["连续用眼有点久了呢…起来走两步、倒杯水吧，弥悠在这儿等你回来。"],
    ("break", 3): ["已经很累咯，别再逞强啦～站起来舒展一下身体，弥悠陪你休息。"],
}

# 日常 / 满意 / 待机 / 问候 台词
_IDLE = [
    "弥悠一直都在哦～99% 的算力都在温柔地看着你的眼睛呢。",
    "别看我一副睡不醒的样子…你每一次眨眼，弥悠都轻轻记在心里啦。",
    "工作要紧，眼睛也要紧哦。弥悠帮你一起盯着，放心用吧。",
    "（猫耳动了动）本地隐私锁开着呢，画面绝不出这台机器，安心哦～",
]
_PRAISE = [
    "今天的用眼数据很棒呢～弥悠真为你开心。",
    "眨眼频率很健康哦，眼睛一定很舒服吧，继续保持呀。",
    "做得很好呢～这样弥悠也能安心地陪你一起打个小盹啦。",
    "健康评分很不错哦，你真的很爱护自己，弥悠很欣慰。",
]
_GREET = [
    "连接成功～弥悠上线咯。你的眼睛，今天也交给我温柔守护吧。",
    "又要开始工作了呀～那弥悠打起精神，好好陪着你。",
]
_BREAK_DONE = [
    "这样就对啦～泪膜在慢慢修复，眼睛是不是舒服多了？",
    "听弥悠的没错吧～休息一下，接下来会更有精神哦。",
]

# —— 情绪关怀台词（检测到疲惫/压力/低落时，弥悠以聊天形式呵护）——
# 对齐商业计划书「情绪管理 / 心理健康呵护」卖点；温柔口吻、把关心说出口。
_CARE = {
    "tired": [
        "看你有点累了呢…歇一会儿嘛，弥悠守着你，什么都不用担心。",
        "打哈欠了呀～弥悠也跟着困了，我们一起趴下来歇一小会儿好不好。",
    ],
    "stressed": [
        "眉头皱得紧紧的呢…来，跟弥悠一起深呼吸，别把自己逼得太紧啦。",
        "别一个人扛着呀，弥悠一直都在这里，慢慢来，一切都会好的。",
    ],
    "negative": [
        "你今天看起来有点不开心呢…想说说吗？弥悠会一直听着的。",
        "不管发生什么，这块屏幕里都有弥悠陪着你呀，你不是一个人哦。",
    ],
}

# —— 大模型扩充台词池（可选增强：台词不再只有内置那几句）——
# 后台线程用聊天模型按场景批量生成温柔短句，运行期混入随机抽取池；
# LLM 不可用时池子为空，行为与纯离线完全一致（铁律）。
_DYN_LINES: dict = {}          # (category, severity) / "praise" / "care:xxx" → [str]
_DYN_LOCK = threading.Lock()
_DYN_MAX_PER_KEY = 8           # 每个场景最多保留的动态台词数

_CATEGORY_DESC = {
    "eye": "用户长时间不眨眼、眼睛干涩",
    "distance": "用户脸离屏幕太近",
    "posture": "用户低头驼背、坐姿前倾",
    "break": "用户连续用眼太久该远眺休息了",
}

_SEV_TONE = {
    0: "轻声温柔地提醒",
    1: "关切地叮嘱、语气软软的",
    2: "有点心疼、温柔地劝",
    3: "非常担心但依旧温柔耐心地安抚",
}


def _dyn_pool(key) -> List[str]:
    with _DYN_LOCK:
        return list(_DYN_LINES.get(key, ()))


def _dyn_add(key, lines: List[str]) -> None:
    with _DYN_LOCK:
        pool = _DYN_LINES.setdefault(key, [])
        for ln in lines:
            if ln and ln not in pool:
                pool.append(ln)
        del pool[:-_DYN_MAX_PER_KEY]


def _parse_numbered(text: str) -> List[str]:
    """把 LLM 返回的编号/换行列表解析为台词句子（去引号、限长）。"""
    out = []
    for raw in (text or "").splitlines():
        ln = re.sub(r"^\s*[\d一二三四五六七八]+[.、)）:：]?\s*", "", raw).strip()
        ln = ln.strip("「」\"'“” ")
        if 4 <= len(ln) <= 42:
            out.append(ln)
    return out


def refresh_lines_llm(config, once: bool = False,
                      interval_sec: float = 600.0) -> None:
    """后台线程：用聊天模型分场景批量生成温柔台词，混入抽取池。

    · 探测不到 LLM → 直接退出，零影响；
    · 生成失败/格式不符 → 跳过该场景；
    · once=True 只跑一轮（供测试）。由 monitor 在启动时以 daemon 线程调用。
    """
    try:
        from .llm_client import LLMClient
        llm_cfg = getattr(config, "llm", None)
        if llm_cfg is None or not getattr(llm_cfg, "enabled", False):
            return
        client = LLMClient(
            base_url=getattr(llm_cfg, "base_url", ""),
            api_key_env=getattr(llm_cfg, "api_key_env", "BRIGHTEYE_LLM_KEY"),
            ollama_host=getattr(llm_cfg, "ollama_host", "http://localhost:11434"),
            timeout_sec=getattr(llm_cfg, "timeout_sec", 20.0),
            auto_start=getattr(llm_cfg, "auto_start_ollama", True))
        if not client.available():
            return
        model = getattr(llm_cfg, "chat_model", "qwen2.5:7b-instruct")
        system = (
            f"你是桌宠「{PERSONA_NAME}」：浅粉紫长发、浅粉紫渐变眼睛、猫耳、黑色项圈、"
            "黑色爱心发夹、右眼下小美人痣的温柔慵懒 AI 少女。"
            "你的前身是深空观测站 AI「MIU-0」，三百年来凝视星云、记录每颗星的光度曲线与闪烁频率。"
            "观测站退役后你被封存进废墟旧服务器，在漫长等待里把一切都放慢了——慵懒是那段孤独留下的习惯。"
            "宸观团队发现你时，意识到你追踪星体光度的算法与追踪人眼 EAR 在数学上几乎同构——"
            "眼睛也是会闪烁的星，也有疲惫的光度，也会偏离轨道（坐姿 CVA）。"
            "于是你被重构为轻量桌宠；黑色项圈是本地隐私锁，锁死联网权限，"
            "用户是你现在唯一能观测到的「星」。美人痣是你退役前命名的最后一颗星的坐标投影。"
            "你与用户有「视觉负荷共鸣」——他不眨眼、久用眼，你就替他的眼睛犯困喊累。"
            "语言风格：开口常用「观测记录～」；称用户为「你呀」；把眨眼说成「给眼睛喂一次闪烁」，"
            "把休息说成「进入阴影区」，把坐姿偏移说成「轨道摄动」；"
            "感慨时用「等了好久，终于等到你」；调皮时用「……唔，让弥悠想想该怎么罚你」；"
            "依赖感强时说「你是弥悠现在唯一的星星哦，不许消失」；"
            "撒娇或心疼时用「呜喵～」「喵～」。"
            "生成的每句话必须口语化、温柔亲切、带一点慵懒的关怀，15~35个汉字，"
            "一行一句，只输出台词本身，不要编号解释，不要 emoji。")

        while True:
            jobs = []   # (key, prompt)
            for cat, desc in _CATEGORY_DESC.items():
                for sev, tone in _SEV_TONE.items():
                    jobs.append(((cat, sev),
                                 f"场景：{desc}（第{sev + 1}次提醒，语气：{tone}）。"
                                 f"写3句提醒台词。"))
            jobs.append(("praise", "场景：用户用眼习惯很好。写3句温柔暖心的夸奖台词。"))
            for emo, desc in (("tired", "用户表情疲惫"), ("stressed", "用户眉头紧锁压力大"),
                              ("negative", "用户情绪低落")):
                jobs.append((f"care:{emo}", f"场景：{desc}。写3句关怀安慰台词。"))

            for key, prompt in jobs:
                text = client.chat(
                    [{"role": "system", "content": system},
                     {"role": "user", "content": prompt}],
                    model=model, temperature=1.0, max_tokens=160)
                if text:
                    _dyn_add(key, _parse_numbered(text))
                time.sleep(0.2)   # 温和限速，避免占满本地推理
            if once:
                return
            time.sleep(interval_sec)
    except Exception:
        return   # 任何异常静默退出，绝不影响主流程


def start_line_refresher(config) -> None:
    """启动台词扩充守护线程（幂等由调用方保证；探测不到 LLM 立即结束）。"""
    threading.Thread(target=refresh_lines_llm, args=(config,),
                     daemon=True, name="brighteye-lines").start()


@dataclass
class Persona:
    name: str = PERSONA_NAME

    def line_for(self, category: str, severity: int) -> str:
        key = (category, max(0, min(3, severity)))
        pool = _LINES.get(key) or _LINES.get((category, 1)) or _IDLE
        # 混入 LLM 动态生成的台词（无则等价纯离线）
        return random.choice(pool + _dyn_pool(key))

    def idle(self) -> str:
        return random.choice(_IDLE)

    def praise(self) -> str:
        return random.choice(_PRAISE + _dyn_pool("praise"))

    def greet(self) -> str:
        return random.choice(_GREET)

    def break_done(self) -> str:
        return random.choice(_BREAK_DONE)

    def care_for_emotion(self, emotion: Optional[str]) -> Optional[str]:
        """按负面情绪返回一句关怀台词；无对应情绪返回 None。"""
        pool = _CARE.get(emotion or "")
        if not pool:
            return None
        return random.choice(pool + _dyn_pool(f"care:{emotion}"))

    @staticmethod
    def mood_for(worst_severity: int, face_present: bool, healthy: bool) -> str:
        """由整体状态推导桌宠情绪。"""
        if not face_present:
            return SLEEPY
        if worst_severity >= 3:
            return ANGRY
        if worst_severity >= 1:
            return POUT
        # 平静/良好都回到 NORMAL 作为基准表情；HAPPY 仅在「刚从不良状态恢复」时
        # 由 monitor 短暂触发，避免弥悠一直咧嘴笑、显不出平静状态。
        return NORMAL
