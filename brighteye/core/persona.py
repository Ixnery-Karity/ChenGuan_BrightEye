"""桌宠角色「弥悠 (Miyu)」的台词与情绪系统（慵懒·傲娇·操碎了心的看护者）。

原创形象设定（详见 docs/弥悠人设.md）：
  浅粉紫长发 · 半睁紫瞳 · 猫耳 · 黑色项圈(本地推理隐私锁) · 黑色桃心发夹 ·
  猫爪动作(45cm 绝对视距防线)。她是宸观 AI 视觉引擎的拟人化中枢，
  与用户建立「视觉负荷共鸣」：你不眨眼、久用眼，她就替你的眼睛犯困喊累。
  性格＝AI 的绝对理性 + 猫系少女的慵懒傲娇；拟声词用「呜喵！/喵惹！」。
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


# —— 各场景台词（弥悠：慵懒傲娇，「视觉负荷共鸣」——她的困=替你的眼睛喊累）——
_LINES = {
    # 久未眨眼 / 干眼（EAR 监测）
    ("eye", 0): ["…你有一会儿没眨眼了哦。弥悠的眼睛都替你觉得干了…眨一下嘛。"],
    ("eye", 1): ["喵惹…你再不眨眼，弥悠都要替你干涩到睁不开了啦。快眨眨眼！"],
    ("eye", 2): ["泪膜都要破裂了啦……再不闭眼休息三十秒，弥悠今天就罢工睡觉了！"],
    ("eye", 3): ["呜喵！！眼睛都干成沙漠了！现在！立刻！用力眨几下！不许讨价还价！"],
    # 距离过近（45cm 绝对视距防线）
    ("distance", 0): ["…再远一点点啦。45 厘米防线，弥悠可是画好了的。"],
    ("distance", 1): ["喂，退后。你的大脸快把弥悠的运算内存挤爆了……"],
    ("distance", 2): ["呜喵！退、退后啦！不许越过 45 厘米的防线！（手忙脚乱地向前推）"],
    ("distance", 3): ["呜喵——！！这么近是想看清弥悠的代码吗？！给、给我坐直退后！"],
    # 坐姿（CVA 颅椎角）
    ("posture", 0): ["背挺直一点啦…别像只软掉的猫那样趴着，弥悠看着都累。"],
    ("posture", 1): ["你的颅椎角快要突破阈值了哦……不要像只虾米一样缩在椅子上。"],
    ("posture", 2): ["喵惹！挺起胸膛！弥悠的项圈都要被你这坐姿气得报警了！"],
    ("posture", 3): ["呜喵！！都歪成这样了还不坐好？！…弥悠会、会担心的啦，笨蛋！"],
    # 该休息 / 久用眼（20-20-20 法则）
    ("break", 0): ["嘀嗒，20 分钟了。去看 20 英尺外的东西 20 秒…比如窗外的云？"],
    ("break", 1): ["该远眺了啦。反正弥悠现在要闭眼省算力了，你也一起休息嘛。"],
    ("break", 2): ["哈呜……连续用眼超载，弥悠的算力快被你榨干了。快起来倒杯水！"],
    ("break", 3): ["（极度困倦）够了！给你三秒钟站起来休息！不然弥悠强行休眠散热了！"],
}

# 日常 / 满意 / 待机 / 问候 台词
_IDLE = [
    "哈呜…弥悠没在偷懒哦，99% 的算力都在盯着你的眼睛呢。",
    "别看我一副睡不醒的样子…你的每一次眨眼，弥悠都记着账呢。",
    "工作归工作，眼睛可只有一双哦。弥悠帮你盯着。",
    "（猫耳动了动）本地隐私锁开着呢，画面绝不出这台机器…安心用吧。",
]
_PRAISE = [
    "唔…今天的用眼数据还不错嘛。才、才没有表扬你哦。",
    "眨眼频率很健康…弥悠难得可以省点算力打个盹了。",
    "保持住啊，难得让弥悠省心一次。",
    "嗯，及格了。健康评分不会骗人…别得意。",
]
_GREET = [
    "连接成功…弥悠上线。你的眼睛，今天也交给我盯着吧。",
    "哈呜…又要开工了吗。那弥悠也只好打起精神陪你了。",
]
_BREAK_DONE = [
    "这还差不多…泪膜修复中，眼睛舒服多了吧？",
    "看吧，听弥悠的没错。…才不是为了夸你。",
]

# —— 情绪关怀台词（检测到疲惫/压力/低落时，弥悠以聊天形式呵护）——
# 对齐商业计划书「情绪管理 / 心理健康呵护」卖点；傲娇口吻但暗含关心。
_CARE = {
    "tired": [
        "…看你一脸累样，弥悠都跟着犯困了啦。歇会儿嘛，我守着你。",
        "打哈欠了哦…视觉负荷共鸣中，弥悠也好困…一起趴一会儿嘛。",
    ],
    "stressed": [
        "眉头皱那么紧做什么…深呼吸一下，别把自己逼太狠啦。",
        "喂，别一个人扛着…弥悠的眼睛里只有你一个人，我在呢。",
    ],
    "negative": [
        "…你今天看起来不太开心。要不要跟弥悠说说？我、我听着呢。",
        "别摆这种表情嘛…笨蛋，至少这块屏幕里还有弥悠陪着你呀。",
    ],
}

# —— 大模型扩充台词池（可选增强：台词不再只有内置那几句）——
# 后台线程用聊天模型按场景批量生成傲娇短句，运行期混入随机抽取池；
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
    0: "轻声提醒、若无其事",
    1: "有点着急、开始嫌弃",
    2: "很不耐烦、命令语气",
    3: "气炸了、大声但透着担心",
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
    """后台线程：用聊天模型分场景批量生成傲娇台词，混入抽取池。

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
            timeout_sec=getattr(llm_cfg, "timeout_sec", 20.0))
        if not client.available():
            return
        model = getattr(llm_cfg, "chat_model", "qwen2.5:7b-instruct")
        system = (
            f"你是桌宠「{PERSONA_NAME}」：浅粉紫长发、半睁紫瞳、猫耳的慵懒傲娇"
            "AI 少女，操碎了心的看护者。你与用户有「视觉负荷共鸣」——他不眨眼、"
            "久用眼，你就替他的眼睛犯困喊累。受惊时用拟声词「呜喵！」「喵惹！」。"
            "生成的每句话必须口语化、带慵懒傲娇语气，15~35个汉字，一行一句，"
            "只输出台词本身，不要编号解释，不要 emoji。")

        while True:
            jobs = []   # (key, prompt)
            for cat, desc in _CATEGORY_DESC.items():
                for sev, tone in _SEV_TONE.items():
                    jobs.append(((cat, sev),
                                 f"场景：{desc}（第{sev + 1}次提醒，语气：{tone}）。"
                                 f"写3句提醒台词。"))
            jobs.append(("praise", "场景：用户用眼习惯很好。写3句傲娇的夸奖台词。"))
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
