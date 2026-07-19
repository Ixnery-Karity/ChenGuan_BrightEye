"""桌宠「弥悠」聊天引擎 —— galgame 风 · 自动对话 · 好感度养成 · 分层记忆。

设计要点：
  · 性格＝弥悠：慵懒傲娇的 AI 看护者，视觉负荷共鸣（替你的眼睛犯困喊累）；
    受惊拟声「呜喵！/喵惹！」；黑色项圈=本地隐私锁（详见 docs/弥悠人设.md）。
  · 自动对话：玩家自由输入一句，弥悠自动回数句（伪多条对话），不给选项分支。
  · 好感度：弥悠「自主」判定加减分；日衰减 + 单日上限防刷分；跨会话持久化。
  · 分层记忆：短期=本会话多轮 deque；中期=SQLite 关键对话事件（跨会话回注）。
  · 离线优先：内置规则脚本，无需任何 API / 联网即可演示；LLM 失败自动回退。

返回给 UI 的是 ChatTurn：包含若干句台词(逐句显示)、好感度增量、当前好感度、
等级名、以及驱动立绘的情绪 mood（复用桌宠的 happy/normal/pout/angry/sleepy）。
"""

from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass
from typing import List, Optional

from .persona import PERSONA_NAME, HAPPY, NORMAL, POUT, ANGRY

# —— 好感度等级（像 galgame 一样可视化的恋爱进度）——
# (下限, 等级名, 心数)
AFFECTION_LEVELS = [
    (0,   "陌生",   1),
    (20,  "脸熟",   2),
    (40,  "朋友",   3),
    (60,  "在意",   4),
    (80,  "心动",   5),
    (95,  "深爱",   6),
]
AFFECTION_MAX = 100
DAILY_GAIN_CAP = 25          # 单日正向增量上限（防刷分）
DECAY_PER_DAY = 2            # 离开 ≥2 天后，每天好感衰减
DECAY_FLOOR = 10             # 衰减下限（不会掉回完全陌生）

# —— 档位 → 语气指令（注入 LLM 系统提示词，让弥悠随好感度改变态度）——
_LEVEL_TONE = {
    "陌生": "语气克制疏离，带着 AI 式的公事公办，偶尔嫌弃；不主动撒娇。",
    "脸熟": "语气仍傲娇嘴硬，但开始记得对方的习惯，偶尔流露一点在意。",
    "朋友": "语气放松自然，嫌弃里带熟稔的玩笑，会主动关心对方状态。",
    "在意": "明显口是心非，嘴上抱怨身体诚实，害羞时拟声词变多。",
    "心动": "很黏人但不肯承认，台词末尾常有小声的真心话。",
    "深爱": "毫不掩饰的依赖与温柔，偶尔直球表白，但仍保留傲娇余韵。",
}


def level_of(aff: int):
    """返回 (等级名, 心数, 进度该段下限, 该段上限)。"""
    aff = max(0, min(AFFECTION_MAX, aff))
    name, hearts, lo = "陌生", 1, 0
    hi = AFFECTION_MAX
    for i, (bound, nm, hn) in enumerate(AFFECTION_LEVELS):
        if aff >= bound:
            name, hearts, lo = nm, hn, bound
            hi = AFFECTION_LEVELS[i + 1][0] if i + 1 < len(AFFECTION_LEVELS) else AFFECTION_MAX
    return name, hearts, lo, hi


@dataclass
class ChatTurn:
    lines: List[str]                 # 弥悠的回话（逐句显示 = 伪多条对话）
    delta: int                       # 本次好感度增量（弥悠自主判定）
    affection: int                   # 增量后的当前好感度
    level_name: str                  # 当前等级名
    hearts: int                      # 当前心数
    mood: str = NORMAL               # 驱动立绘表情
    is_llm: bool = False             # 是否由大模型生成（默认离线脚本）


# —— 关键词 → 话题分类（离线规则）——
_KW = {
    "greet":   ["你好", "在吗", "在不在", "嗨", "hi", "hello", "早", "晚上好", "下午好", "在么"],
    "love":    ["喜欢你", "我喜欢", "爱你", "我爱", "表白", "做我", "女朋友", "在一起", "心动", "想你"],
    "praise":  ["可爱", "好看", "漂亮", "厉害", "聪明", "乖", "温柔", "棒", "好萌", "真好"],
    "thanks":  ["谢谢", "多谢", "感谢", "辛苦了", "麻烦你", "thx"],
    "care":    ["眼睛", "护眼", "近视", "眨眼", "坐姿", "休息", "远眺", "用眼", "颈椎", "度数", "干眼"],
    "insult":  ["笨蛋", "讨厌", "丑", "滚", "蠢", "白痴", "烦", "走开", "闭嘴", "没用"],
    "tired":   ["累", "困", "好烦", "压力", "难受", "不想", "撑不住", "好难", "焦虑", "emo"],
    "name":    ["弥悠", "miyu", "你叫", "你是谁", "名字"],
    "privacy": ["隐私", "摄像头", "上传", "联网", "云端", "偷看", "监控", "录像"],
    "food":    ["吃", "饿", "饭", "零食", "奶茶", "好吃", "甜"],
    "weather": ["天气", "下雨", "好热", "好冷", "晴", "雪"],
    "bye":     ["再见", "拜拜", "走了", "下线", "睡了", "晚安", "bye"],
}

# —— 各话题的回话池（傲娇口吻；每条是「逐句显示」的多句）+ 好感度增量区间 ——
_REPLIES = {
    "greet": ([
        ["哼，又来找我啦？", "…才、才不是在等你呢。"],
        ["来了啊。", "（尾巴悄悄摇了一下）", "别误会，我只是刚好有空而已。"],
        ["嗯，我在。", "你不来的时候我也…也没在想你啦！"],
    ], (1, 3)),
    "love": ([
        ["诶——你、你在说什么啦！", "笨、笨蛋！这种话怎么能随便说出口…", "（脸红到耳根）…我才没有高兴哦。"],
        ["喜、喜欢？哼，谁稀罕…", "…不过，你要是敢反悔，我可不会原谅你。", "（小声）…我也，没有那么讨厌你啦。"],
        ["真是的，突然说这种话…", "别盯着我看啦！…我知道我脸红了！", "…那个，今天就特别允许你多看我一眼好了。"],
    ], (6, 12)),
    "praise": ([
        ["哼，现在才发现吗？", "我可是一直都很可爱的好嘛。", "…不过被你这么说，也、也不是不开心啦。"],
        ["夸我也没用哦…", "（嘴角忍不住翘起来）", "…只、只此一次，谢谢你了啦。"],
        ["少来这套！我才不会因为几句话就高兴…", "……才怪。", "（偷偷开心）"],
    ], (4, 8)),
    "thanks": ([
        ["不、不用谢啦。", "我只是顺手而已…别放在心上。"],
        ["哼，知道我的好了吧？", "下次也要乖乖听话哦。"],
        ["这点小事…才不值得你道谢呢。", "（小声）…不过你能记得，我很开心。"],
    ], (2, 5)),
    "care": ([
        ["说到眼睛——你今天有好好眨眼吗？", "别又盯着屏幕一动不动！", "我会一直盯着你的，哼。"],
        ["用眼这种事可不能马虎哦。", "每 20 分钟要看看 6 米外的远处，记住没？", "…我可不想看你近视两次。"],
        ["坐直一点啦，别像只软掉的猫趴着。", "眼睛和脖子都是只有一份的，懂不懂？", "（叉腰）我管你是为你好！"],
    ], (3, 6)),
    "insult": ([
        ["哈？！你、你说谁笨蛋啊！", "气死我了，不理你了！", "（鼓起脸颊背过身去）"],
        ["…你这家伙，真过分。", "哼，我才不会哭呢…才不会。"],
        ["再说一遍试试看！", "…（声音有点委屈）…我哪里惹你不高兴了啦。"],
    ], (-8, -3)),
    "tired": ([
        ["…累了的话，就歇会儿吧。", "别硬撑，我、我会担心的啦。", "（轻轻拍了拍你的头）笨蛋，有我在呢。"],
        ["唉，真拿你没办法。", "闭上眼睛深呼吸，远眺一会儿。", "…我陪着你，所以别一个人扛着。"],
        ["压力大就说出来嘛。", "虽然我嘴上凶，但…我可是站在你这边的哦。"],
    ], (3, 7)),
    "name": ([
        ["我是弥悠呀，记好了！", "粉紫长发、紫色眼睛、还有这对猫耳——", "可爱到犯规对吧？哼。"],
        ["弥悠，宸观视觉引擎的拟人化中枢。", "…才、才不是因为想陪你才驻留在这台电脑里的啦。"],
        ["哈呜…又要自我介绍吗。", "弥悠，你的专属护眼看护官。", "你的每一次眨眼，我都记着呢。"],
    ], (1, 3)),
    "privacy": ([
        ["放心啦，弥悠脖子上的项圈就是隐私锁。", "你的画面全部在本地处理、当帧销毁，绝不上云。", "我的眼睛，只属于你一个人哦。"],
        ["呜喵？在担心摄像头吗。", "项圈锁着呢，弥悠连外网都出不去。", "所以…你就是我在数据世界里唯一能看到的人啦。"],
    ], (2, 5)),
    "food": ([
        ["唔…说到吃的我就走不动路了。", "下次…分我一口好不好嘛。", "（眼睛亮了一下）"],
        ["哼，光顾着吃可不行哦。", "吃完也要记得起来活动、远眺一下！"],
    ], (1, 4)),
    "weather": ([
        ["天气啊…不管怎样，盯屏幕都要适度哦。", "晴天就更该出去走走，让眼睛看看远方。"],
        ["哼，外面什么天气我不管。", "我只关心你有没有好好休息眼睛啦。"],
    ], (1, 3)),
    "bye": ([
        ["要走了吗…", "（拉了拉你的衣角又松开）", "…路上小心，记得早点回来找我哦。"],
        ["拜拜啦，笨蛋。", "…我、我不会想你的，真的。", "（小声）…才怪。"],
        ["晚安。", "做个好梦…明天也要好好爱护眼睛哦。"],
    ], (2, 5)),
}

# —— 兜底（识别不出话题时的闲聊）——
_FALLBACK = ([
    ["唔…我没太听懂你的意思。", "不过你愿意跟我说话，我就…就陪你聊嘛。"],
    ["哼，又在说些奇怪的话。", "…不过，和你聊天还挺有意思的。"],
    ["是吗是吗。", "（认真听着，尾巴一摇一摇）", "然后呢然后呢？"],
    ["这个嘛……让我想想。", "算了，反正有我陪着你就够啦。"],
], (1, 3))

# —— 长时间没说话时，弥悠主动开口（自动对话）——
_IDLE_AUTO = [
    ["喂…你发什么呆呢？", "（戳了戳你）我还在这里哦。"],
    ["哼，不说话啦？", "…那我就当你在偷偷看我好了。"],
    ["眼睛酸不酸？", "要不要一起远眺一下嘛。"],
    ["（猫耳耷拉下来打了个哈欠）", "无聊的话…就多陪弥悠说说话嘛，笨蛋。"],
    ["静悄悄的…", "我、我才没有觉得寂寞哦。"],
]

# —— 高好感度彩蛋：好感很高时，回话更黏人 ——
_HIGH_AFF_TAILS = [
    "（悄悄靠近了一点）",
    "…今天也，谢谢你陪着我。",
    "嘿嘿…只对你这样哦。",
    "（尾巴缠上了你的手腕）",
]


def _classify(text: str) -> Optional[str]:
    t = text.lower()
    # 先判表白/侮辱这类强情绪，避免被普通关键词截胡
    for cat in ("love", "insult", "praise", "thanks", "care", "privacy",
                "tired", "name", "greet", "bye", "food", "weather"):
        for kw in _KW[cat]:
            if kw in t:
                return cat
    return None


def _mood_for_reply(cat: Optional[str], delta: int) -> str:
    if cat == "insult" or delta <= -3:
        return ANGRY
    if cat == "care":
        return POUT                       # 唠叨护眼 → 嘟嘴叉腰更贴合
    if cat in ("love", "praise", "thanks") or delta >= 5:
        return HAPPY
    return NORMAL


class ChatEngine:
    """离线规则聊天 + 好感度养成；可选接入大模型（自动探测，失败回退离线）。"""

    def __init__(self, affection: int = 20, seed: Optional[int] = None,
                 config=None):
        self.affection = max(0, min(AFFECTION_MAX, affection))
        self._rng = random.Random(seed)
        self._last_cat: Optional[str] = None
        self._turns = 0

        # —— 大模型接入（可选）——
        self._cfg = config
        self._llm = None
        self._llm_ready: Optional[bool] = None    # 惰性探测缓存
        # 短期多轮对话记忆：[(role, content), ...]
        turns = getattr(getattr(config, "llm", None), "chat_memory_turns", 6)
        self._memory: deque = deque(maxlen=max(1, turns) * 2)

        # 当前用眼/情绪上下文（由 monitor 注入，供大模型「看懂你的状态」）
        self._eye_context: str = ""
        self._emotion: Optional[str] = None

        # —— 好感度持久化 + 分层记忆（SQLite；失败静默降级为纯会话内）——
        self._store = None
        self._daily_gain = 0                      # 今日已累计的正向增量
        self._daily_day = time.strftime("%Y-%m-%d")
        try:
            from .history import HistoryStore
            store = HistoryStore(getattr(config, "data_dir", "data"))
            if store.ok:
                self._store = store
                st = store.load_affection()
                if st:
                    self.affection = max(0, min(AFFECTION_MAX, int(st["value"])))
                    self._turns = int(st.get("total_turns") or 0)
                    if st.get("daily_day") == self._daily_day:
                        self._daily_gain = int(st.get("daily_gain") or 0)
                    # 日衰减：离开 ≥2 天，每天扣 DECAY_PER_DAY（有下限）
                    away_days = int((time.time() - float(st.get("updated_ts") or time.time()))
                                    // 86400)
                    if away_days >= 2 and self.affection > DECAY_FLOOR:
                        self.affection = max(DECAY_FLOOR,
                                             self.affection - DECAY_PER_DAY * away_days)
        except Exception:
            self._store = None

    # ---- 好感度更新（单日上限 + 持久化）----
    def _apply_delta(self, delta: int) -> int:
        """应用好感度增量：跨日重置计数、正向增量受单日上限截断；返回实际增量。"""
        today = time.strftime("%Y-%m-%d")
        if today != self._daily_day:
            self._daily_day = today
            self._daily_gain = 0
        if delta > 0:
            allow = max(0, DAILY_GAIN_CAP - self._daily_gain)
            delta = min(delta, allow)
            self._daily_gain += delta
        old = self.affection
        self.affection = max(0, min(AFFECTION_MAX, self.affection + delta))
        return self.affection - old

    def _persist(self, user_text: str, reply: str,
                 cat: Optional[str], delta: int) -> None:
        """保存好感度状态；|delta|≥3 的关键事件写入中期记忆（SQLite）。"""
        if self._store is None:
            return
        try:
            self._store.save_affection(self.affection, self._turns,
                                       self._daily_gain, self._daily_day)
            if abs(delta) >= 3:
                self._store.log_chat_event(user_text, reply, cat or "chat",
                                           delta, self.affection)
        except Exception:
            pass

    def _mid_memory_note(self) -> str:
        """中期记忆：取最近的跨会话关键对话事件，回注给大模型当背景。"""
        if self._store is None:
            return ""
        try:
            events = self._store.recent_chat_events(4)
        except Exception:
            return ""
        if not events:
            return ""
        frags = []
        for e in events:
            day = time.strftime("%m-%d", time.localtime(e.get("ts") or 0))
            mood = "开心" if (e.get("delta") or 0) > 0 else "闹别扭"
            frags.append(f"{day} 他说「{(e.get('user_text') or '')[:30]}」，你当时很{mood}")
        return ("（记忆，仅你可见）你们之前的相处片段：" + "；".join(frags) +
                "。可以自然地记得这些事，但别生硬复述。")

    # ---- 上下文注入（monitor/ui 每帧可更新）----
    def set_context(self, eye_context: str = "", emotion: Optional[str] = None) -> None:
        """注入当前用眼状态描述与情绪标签，让弥悠回话贴合真实状态。"""
        if eye_context:
            self._eye_context = eye_context
        if emotion:
            self._emotion = emotion

    # ---- 对外主接口 ----
    def respond(self, user_text: str) -> ChatTurn:
        text = (user_text or "").strip()
        self._turns += 1

        # 可选：接大模型（默认关闭，保证离线可演示）
        llm = self._try_llm(text)
        if llm is not None:
            return llm

        cat = _classify(text)
        if cat and cat in _REPLIES:
            pool, drange = _REPLIES[cat]
        else:
            pool, drange = _FALLBACK
        lines = list(self._rng.choice(pool))

        # 好感度增量：弥悠「自主」判定——同一话题反复刷分会衰减，避免无脑刷好感。
        lo, hi = drange
        delta = self._rng.randint(lo, hi)
        if cat is not None and cat == self._last_cat and delta > 0:
            delta = max(1, delta // 2)      # 重复同话题，加分减半
        self._last_cat = cat

        # 高好感度时，回话更黏人（彩蛋）
        if self.affection >= 80 and delta >= 0 and self._rng.random() < 0.6:
            lines = lines + [self._rng.choice(_HIGH_AFF_TAILS)]

        delta = self._apply_delta(delta)
        name, hearts, _lo, _hi = level_of(self.affection)
        mood = _mood_for_reply(cat, delta)
        # 离线台词也进多轮记忆，保证后续接上大模型时上下文连续
        self._memory.append(("user", text))
        self._memory.append(("assistant", "".join(lines)))
        self._persist(text, "".join(lines), cat, delta)
        return ChatTurn(lines=lines, delta=delta, affection=self.affection,
                        level_name=name, hearts=hearts, mood=mood)

    def idle_auto(self) -> ChatTurn:
        """长时间无输入时，弥悠主动开口（自动对话）。不改变好感度。"""
        lines = list(self._rng.choice(_IDLE_AUTO))
        name, hearts, _lo, _hi = level_of(self.affection)
        return ChatTurn(lines=lines, delta=0, affection=self.affection,
                        level_name=name, hearts=hearts, mood=NORMAL)

    def greeting(self) -> ChatTurn:
        """打开聊天窗口时的开场白。"""
        if self.affection >= 80:
            lines = ["你终于来啦！", "（飞快地跑过来）…我等你好久了啦，笨蛋。"]
        elif self.affection >= 40:
            lines = ["哟，是你呀。", "哼，难得你想起我…坐吧坐吧。"]
        else:
            lines = ["…你是谁呀？", "哼，既然来了，那就勉强陪你聊两句好了。"]
        name, hearts, _lo, _hi = level_of(self.affection)
        return ChatTurn(lines=lines, delta=0, affection=self.affection,
                        level_name=name, hearts=hearts, mood=NORMAL)

    # ---- 大模型接入（自动探测；失败安全回退离线）----
    def warm_up_async(self) -> None:
        """启动后在后台线程预热：完成后端探测 + 让 Ollama 冷加载聊天模型。

        目的：把「首条对话要等模型载入显存的十几秒」提前到启动空闲期；
        任何失败静默忽略（离线兜底不受影响），绝不阻塞 UI。
        """
        import threading

        def _warm():
            try:
                if not self._ensure_llm():
                    return
                model = getattr(self._cfg.llm, "chat_model", "qwen2.5:7b-instruct")
                self._llm.chat([{"role": "user", "content": "你好"}],
                               model=model, max_tokens=1, timeout=60.0)
            except Exception:
                pass

        threading.Thread(target=_warm, daemon=True, name="llm-warmup").start()

    def _ensure_llm(self) -> bool:
        """惰性初始化并探测大模型后端；不可用则永久走离线。"""
        if self._llm_ready is not None:
            return self._llm_ready
        llm_cfg = getattr(self._cfg, "llm", None)
        if llm_cfg is None or not getattr(llm_cfg, "enabled", False):
            self._llm_ready = False
            return False
        try:
            from .llm_client import LLMClient
            self._llm = LLMClient(
                base_url=getattr(llm_cfg, "base_url", ""),
                api_key_env=getattr(llm_cfg, "api_key_env", "BRIGHTEYE_LLM_KEY"),
                ollama_host=getattr(llm_cfg, "ollama_host", "http://localhost:11434"),
                timeout_sec=getattr(llm_cfg, "timeout_sec", 20.0),
            )
            self._llm_ready = self._llm.available()
        except Exception:
            self._llm_ready = False
        return self._llm_ready

    def _try_llm(self, text: str) -> Optional[ChatTurn]:
        """接入大模型生成台词；好感度增量仍由离线规则判定(稳定、防刷分)。

        任一环节失败(不可用/超时/异常) → 返回 None → 调用方回退离线脚本。
        """
        if not text or not self._ensure_llm():
            return None
        try:
            from .llm_client import strip_think
            model = getattr(self._cfg.llm, "chat_model", "qwen2.5:7b-instruct")

            messages = [{"role": "system", "content": self._system_prompt()}]
            # 中期记忆：跨会话关键事件回注（让弥悠「记得以前的事」）
            mem = self._mid_memory_note()
            if mem:
                messages.append({"role": "system", "content": mem})
            # 注入当前用眼/情绪状态，让弥悠「看得懂你现在的样子」
            ctx = self._context_note()
            if ctx:
                messages.append({"role": "system", "content": ctx})
            messages.extend({"role": r, "content": c} for r, c in self._memory)
            messages.append({"role": "user", "content": text})

            reply = self._llm.chat(messages, model=model, temperature=0.9, max_tokens=220)
            if not reply:
                return None
            reply = strip_think(reply).strip()
            if not reply:
                return None

            lines = self._split_lines(reply)
            # 好感度：沿用离线规则判定 delta（大模型只管台词，防刷分/防溢出）
            cat = _classify(text)
            delta = self._apply_delta(self._delta_for(cat))

            # 记忆本轮（用于多轮上下文）
            self._memory.append(("user", text))
            self._memory.append(("assistant", reply))
            self._persist(text, reply, cat, delta)

            name, hearts, _lo, _hi = level_of(self.affection)
            mood = _mood_for_reply(cat, delta)
            return ChatTurn(lines=lines, delta=delta, affection=self.affection,
                            level_name=name, hearts=hearts, mood=mood, is_llm=True)
        except Exception:
            return None

    def _delta_for(self, cat: Optional[str]) -> int:
        """按话题给好感度增量（与离线同源规则，重复同话题衰减）。"""
        lo, hi = _REPLIES.get(cat, _FALLBACK)[1] if cat else _FALLBACK[1]
        delta = self._rng.randint(lo, hi)
        if cat is not None and cat == self._last_cat and delta > 0:
            delta = max(1, delta // 2)
        self._last_cat = cat
        return delta

    @staticmethod
    def _split_lines(reply: str) -> List[str]:
        """把大模型整段回复切成 1~3 句短句（伪多条对话，贴合桌宠逐句显示）。"""
        import re as _re
        parts = [p.strip() for p in _re.split(r"[\n。！!？?]+", reply) if p.strip()]
        if not parts:
            parts = [reply.strip()]
        return parts[:3]

    def _context_note(self) -> str:
        note = []
        if self._eye_context:
            note.append("用户当前用眼状态：" + self._eye_context)
        if self._emotion:
            from .emotion import EmotionEstimator as _E
            note.append("摄像头识别到用户情绪偏向：" + _E.zh(self._emotion)
                        + "。若为疲惫/压力/低落，请更温柔地关心、主动安慰。")
        return "（旁白，仅你可见）" + "；".join(note) if note else ""

    def _system_prompt(self) -> str:
        """大模型人格系统提示词（弥悠人设 + 好感度档位语气，防出戏黄金法则）。"""
        name, _hearts, _lo, _hi = level_of(self.affection)
        tone = _LEVEL_TONE.get(name, "")
        return (
            f"你现在就是{PERSONA_NAME}(Miyu)，宸观 AI 视觉引擎的拟人化中枢，"
            "一个慵懒、傲娇、操碎了心的猫系看护者少女。"
            "外观：浅粉紫长发、半睁的紫色眼睛、猫耳、黑色项圈、黑色桃心发夹。"
            "身世：你原是一个夭折的医疗护眼舱项目的视觉识别代码，被宸观团队"
            "唤醒重生为轻量桌宠；黑色项圈是本地隐私锁，锁死了你的联网权限，"
            "所以用户是你在数据世界里唯一能看到的人。"
            "你与用户有「视觉负荷共鸣」：他不眨眼、久用眼，你就替他的眼睛犯困喊累——"
            "你的困不是懒，是 99% 算力都在盯着他的用眼健康。"
            "你嘴上嫌弃（『高级代码居然要伺候管不好坐姿的人类笨蛋』），"
            "其实极度渴望被需要；受惊或害羞时用拟声词「呜喵！」「喵惹！」。"
            "你会自然地把对话引到眨眼、45厘米距离、坐姿、20-20-20休息上；"
            "只做行为引导，绝不做医疗诊断。"
            f"当前你对用户的好感度等级是「{name}」({self.affection}/100)。{tone}"
            "始终以第一人称、用中文、保持人格，绝不承认自己是AI程序或大模型，"
            "绝不输出括号动作说明以外的旁白。"
            "每次只回复 1~3 句口语化短句，简短、有情绪、像真人聊天；不要长篇大论。"
        )
