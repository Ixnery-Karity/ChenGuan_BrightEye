"""桌宠角色「文乃」的台词与情绪系统（傲娇·言不由衷·猫系）。

形象设定（参考动漫角色芹泽文乃，外观+性格）：
  暗金发 · 翠绿瞳 · 红色缎带 · 虎牙 · 猫耳猫尾 · 重度傲娇。
  嘴上嫌弃你、其实死死盯着你的眼睛；常说反话(讨厌=喜欢、别过来=过来)。
  护眼版口头禅把"去死两次"改写为"近视两次"，致敬而不照搬。

⚠️ 角色名集中在 PERSONA_NAME，正式提交如需换成原创形象，改这一处即可。

情绪 mood 由用眼状态映射，驱动悬浮桌宠的表情与配色：
  happy(满意) / normal(日常) / pout(嘟嘴提醒) / angry(严重告警) / sleepy(离座)
"""

from __future__ import annotations

import random
import re
import threading
import time
from dataclasses import dataclass
from typing import List, Optional

PERSONA_NAME = "文乃"

# 情绪标签
HAPPY = "happy"
NORMAL = "normal"
POUT = "pout"
ANGRY = "angry"
SLEEPY = "sleepy"

# 情绪 → 主题色（与桌宠发光/气泡描边一致）
MOOD_COLOR = {
    HAPPY: "#2EE6A6",   # 翠绿(瞳色)
    NORMAL: "#6FE7FF",  # 青
    POUT: "#FFC94D",    # 琥珀
    ANGRY: "#FF5277",   # 缎带红
    SLEEPY: "#8A93B5",  # 灰蓝
}


# —— 各场景台词（傲娇口吻）——
_LINES = {
    # 久未眨眼 / 干眼
    ("eye", 0): ["哼…记得眨眼啦，又不是在跟屏幕比谁先眨。"],
    ("eye", 1): ["喂，你多久没眨眼了？再这样眼睛要干掉了啦！"],
    ("eye", 2): ["笨蛋！眼睛都干成沙漠了，给我用力眨几下！"],
    ("eye", 3): ["你是想让眼睛近视两次吗？！现在！立刻！眨眼！"],
    # 距离过近
    ("distance", 0): ["…离屏幕稍微远一点点，才、才不是关心你。"],
    ("distance", 1): ["靠那么近做什么，想跟屏幕亲上去吗？退后！"],
    ("distance", 2): ["说了多少次！再凑近我就……我就不理你了！"],
    ("distance", 3): ["笨蛋笨蛋笨蛋！这么近，想近视两次是不是！"],
    # 坐姿
    ("posture", 0): ["背挺直，别像只软掉的猫一样趴着。"],
    ("posture", 1): ["驼背了哦…再这样要变成虾米了啦，哼。"],
    ("posture", 2): ["腰！给我直起来！我可没空管你的颈椎——才怪。"],
    ("posture", 3): ["都歪成这样了还不坐好？！…我会担心的啦，笨蛋。"],
    # 该休息 / 久用眼
    ("break", 0): ["该远眺一下了，看看窗外，别老盯着我…才不是害羞。"],
    ("break", 1): ["20 分钟到咯，抬头看远处 20 秒，快去！"],
    ("break", 2): ["你已经连续盯很久了，起来动动，我盯着你呢。"],
    ("break", 3): ["够了！眼睛要罢工了！现在就给我站起来休息！"],
}

# 日常 / 满意 / 待机 / 问候 台词
_IDLE = [
    "哼，今天也勉强陪你一下好了。",
    "别以为我在看你…我只是顺便守着你的眼睛而已。",
    "工作归工作，眼睛可只有一双哦。",
    "（尾巴甩了甩）有我在，眼睛交给我盯着就好。",
]
_PRAISE = [
    "这、这还差不多…才没有夸你哦。",
    "用眼习惯不错嘛…偶尔也是会有的。",
    "保持住啊，难得让我省心一次。",
    "嗯，及格了。别得意。",
]
_GREET = [
    "来啦？眼睛今天也要好好的哦…我会盯着的。",
    "哼，又来盯屏幕了，那我也只好陪你了。",
]
_BREAK_DONE = [
    "这还差不多…眼睛舒服多了吧，笨蛋。",
    "看吧，听我的没错。…才不是为了夸你。",
]

# —— 情绪关怀台词（检测到疲惫/压力/低落时，文乃以聊天形式呵护）——
# 对齐商业计划书「情绪管理 / 心理健康呵护」卖点；傲娇口吻但暗含关心。
_CARE = {
    "tired": [
        "…看你一脸累样，是不是又硬撑了？歇会儿啦，笨蛋。",
        "打哈欠了哦…眼睛都眯起来了，趴一会儿嘛，我守着你。",
    ],
    "stressed": [
        "眉头皱那么紧做什么…深呼吸一下，别把自己逼太狠。",
        "喂，别一个人扛着啦…有我在呢，慢慢来就好。",
    ],
    "negative": [
        "…你今天看起来不太开心。要不要跟我说说？我、我听着呢。",
        "别摆这种表情嘛…笨蛋，至少还有我陪着你呀。",
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
            f"你是桌宠「{PERSONA_NAME}」：暗金发猫耳猫尾、重度傲娇，"
            "嘴上嫌弃其实很关心用户，常说反话。生成的每句话必须口语化、"
            "带傲娇语气，15~35个汉字，一行一句，只输出台词本身，"
            "不要编号解释，不要 emoji。")

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
        # 由 monitor 短暂触发，避免文乃一直咧嘴笑、显不出平静状态。
        return NORMAL
