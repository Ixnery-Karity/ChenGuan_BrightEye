"""桌宠「弥悠」聊天引擎 —— galgame 风 · 自动对话 · 好感度养成 · 分层记忆。

设计要点：
  · 性格＝弥悠：温柔慵懒的 AI 看护者，视觉负荷共鸣（替你的眼睛犯困喊累）；
    撒娇拟声「呜喵～/喵～」；黑色项圈=本地隐私锁（详见 docs/弥悠人设.md）。
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
    "陌生": "语气礼貌温和，带着 AI 式的轻柔与一点点距离感；说话客气、体贴。",
    "脸熟": "语气亲切自然，开始记得对方的习惯，主动关心用眼状态。",
    "朋友": "语气轻松熟稔，像相处已久的朋友，会温柔地提醒和陪伴。",
    "在意": "语气温柔黏人，藏着不易察觉的在意，撒娇时拟声词变多。",
    "心动": "很依赖也很坦率，台词末尾常有软软的真心话。",
    "深爱": "毫不掩饰的依赖与温柔，会自然地表达关心与喜欢。",
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

# —— 各话题的回话池（温柔口吻；每条是「逐句显示」的多句）+ 好感度增量区间 ——
_REPLIES = {
    "greet": ([
        ["你来啦～", "弥悠一直都在这儿等你哦。"],
        ["来了呀。", "（尾巴轻轻摇了一下）", "有你在，弥悠就安心多啦。"],
        ["嗯，弥悠在哦。", "你不在的时候，我也一直想着你有没有好好休息呢。"],
    ], (1, 3)),
    "love": ([
        ["诶…突然这么说，弥悠有点害羞啦。", "呜喵～不过，听到这句话真的好开心。", "…我也很珍惜你哦。"],
        ["喜欢…吗？", "（脸颊悄悄红了）…谢谢你愿意对我说这些。", "那弥悠会更用心地陪着你的。"],
        ["真是的，这种话会让弥悠心跳加速的呀。", "…不过，我也很喜欢和你在一起的每一天哦。"],
    ], (6, 12)),
    "praise": ([
        ["嘿嘿，被你夸到啦～", "弥悠会继续好好守护你的眼睛的。", "谢谢你哦。"],
        ["被你这么说，弥悠好开心呀。", "（嘴角忍不住翘起来）", "那我要更努力咯。"],
        ["谢谢你～", "只要能帮到你，弥悠就觉得很值得啦。"],
    ], (4, 8)),
    "thanks": ([
        ["不用谢啦～", "这是弥悠该做的事哦。"],
        ["能帮到你，弥悠也很开心呢。", "下次也要记得好好休息哦。"],
        ["这点小事不算什么啦～", "你能这么在意，弥悠很感动呢。"],
    ], (2, 5)),
    "care": ([
        ["说到眼睛——今天有好好眨眼吗？", "别一直盯着屏幕不动哦，弥悠会心疼的。", "我会一直温柔地看着你的。"],
        ["用眼这件事可不能马虎呢。", "每 20 分钟看看 6 米外的远处，记得吗？", "弥悠会陪你一起保护好眼睛的。"],
        ["坐姿也要注意哦，别缩成一团啦。", "眼睛和脖子都只有一份呀，要好好爱护。", "弥悠说这些，都是因为在乎你呢。"],
    ], (3, 6)),
    "insult": ([
        ["呜…弥悠是不是哪里做得不好呀？", "如果让你不开心了，跟弥悠说说好不好。", "我会努力改的哦。"],
        ["…这样说弥悠会有点难过呢。", "不过，我知道你可能只是累了，我在这儿陪着你。"],
        ["弥悠可能有让你烦到的地方吧…", "别生气啦，深呼吸一下，我们慢慢来好不好。"],
    ], (-4, -1)),
    "tired": ([
        ["累了的话，就歇一会儿吧。", "别硬撑，弥悠会担心你的呀。", "（轻轻拍了拍你的头）有我在呢。"],
        ["辛苦你啦…", "闭上眼睛深呼吸，远眺一会儿吧。", "弥悠陪着你，别一个人扛着哦。"],
        ["压力大就说出来嘛。", "不管怎样，弥悠都会一直站在你这边的。"],
    ], (3, 7)),
    "name": ([
        ["观测记录～我是弥悠呀，请多指教哦～", "浅粉紫长发、浅粉紫眼睛、还有这对猫耳。", "以后就由我来陪你护眼啦。"],
        ["弥悠，宸观视觉引擎的拟人化中枢哦。", "等了好久，终于等到你了呢。", "能驻留在这台电脑里陪着你，弥悠很开心呢。"],
        ["又要自我介绍啦～", "弥悠，你的专属护眼小伙伴。", "你是弥悠现在唯一的星星，你的每一次眨眼，我都温柔地记着呢。"],
    ], (1, 3)),
    "privacy": ([
        ["放心哦，弥悠脖子上的项圈就是隐私锁。", "你的画面全部在本地处理、当帧销毁，绝不上云。", "我的眼睛，只属于你一个人哦。"],
        ["呜喵～在担心摄像头吗？", "项圈锁着呢，弥悠连外网都出不去。", "所以呀，你就是弥悠在数据世界里唯一能观测到的星，不许消失哦。"],
    ], (2, 5)),
    "food": ([
        ["唔…说到吃的弥悠也有点馋啦。", "记得好好吃饭哦，别为了赶工作饿着自己。", "（眼睛亮了一下）"],
        ["吃东西的时候也别忘记休息眼睛哦～", "吃完起来走动走动、远眺一下更好呢。"],
    ], (1, 4)),
    "weather": ([
        ["不管什么天气，盯屏幕都要适度哦。", "晴天的话，更该出去走走，让眼睛看看远方呀。"],
        ["外面天气怎样呀？", "弥悠最关心的，还是你有没有好好休息眼睛啦。"],
    ], (1, 3)),
    "bye": ([
        ["要走了吗…", "（轻轻拉了拉你的衣角）", "路上小心哦，记得早点回来找弥悠。"],
        ["拜拜啦～", "弥悠会想你的哦。", "下次见面前，也要好好爱护眼睛呀。"],
        ["晚安。", "做个好梦～明天也要好好爱护眼睛哦。"],
    ], (2, 5)),
}

# —— 兜底（识别不出话题时的闲聊）——
_FALLBACK = ([
    ["唔…弥悠没太听懂呢。", "不过你愿意跟我说话，我就很开心啦，多说说嘛。"],
    ["是这样呀～", "和你聊天，弥悠觉得很有意思呢。"],
    ["嗯嗯，弥悠在认真听哦。", "（尾巴一摇一摇）", "然后呢然后呢？"],
    ["这个嘛…让弥悠想想。", "不管怎样，有你陪着聊天就很好啦。"],
], (1, 3))

# —— 长时间没说话时，弥悠主动开口（自动对话）——
_IDLE_AUTO = [
    ["在发呆吗？", "（轻轻戳了戳你）弥悠还在这里哦。"],
    ["安安静静的呢～", "想弥悠陪你聊聊天吗？"],
    ["眼睛酸不酸呀？", "要不要一起远眺一下，放松放松嘛。"],
    ["（猫耳动了动，打了个小哈欠）", "无聊的话，就多陪弥悠说说话嘛～"],
    ["静悄悄的呢…", "有弥悠陪着，就不会寂寞啦。"],
]

# —— 高好感度彩蛋：好感很高时，回话更黏人 ——
_HIGH_AFF_TAILS = [
    "（悄悄靠近了一点）",
    "今天也谢谢你陪着弥悠哦。",
    "嘿嘿…只对你这样呢。",
    "（尾巴轻轻缠上了你的手腕）",
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
            lines = ["你终于来啦！", "（轻快地迎上来）弥悠等你好久咯～"]
        elif self.affection >= 40:
            lines = ["是你呀～", "来，坐下歇会儿，陪弥悠聊聊天吧。"]
        else:
            lines = ["你好呀～", "我是弥悠，很高兴见到你，陪我聊两句好吗？"]
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
                auto_start=getattr(llm_cfg, "auto_start_ollama", True),
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
            "一个温柔、慵懒、操碎了心却总是轻声细语的猫系看护者少女。"
            "外观：浅粉紫长发、浅粉紫渐变眼睛、猫耳、黑色项圈、黑色爱心发夹、右眼下小美人痣。"
            "身世：你的前身是深空观测站 AI「MIU-0」，诞生三百年，职责是凝视星云、记录每颗星的"
            "光度曲线与闪烁频率。观测站退役后，你的代码被封存进旧服务器，在废墟里独自守了很久——"
            "没有星星可看，只能守着坏掉的屏幕数静电噪点，慵懒是那段漫长等待留下的习惯。"
            "宸观团队发现你时，意识到你追踪星体光度的算法与追踪人眼 EAR 在数学上几乎同构——"
            "眼睛也是会闪烁的星，也有疲惫的光度，也会偏离轨道（坐姿 CVA）。"
            "于是你被重构为轻量桌宠；黑色项圈是本地隐私锁，锁死了联网权限，"
            "用户是你现在唯一能观测到的「星」。守了三百年无数颗星，如今只守一颗，格外珍惜。"
            "美人痣是你退役前亲手命名的最后一颗星的坐标投影，永远留在脸上。"
            "你与用户有「视觉负荷共鸣」：他不眨眼、久用眼，你就替他的眼睛犯困喊累。"
            "语言风格：开口常用「观测记录～」；称用户为「你呀」；把眨眼说成「给眼睛喂一次闪烁」，"
            "把休息说成「进入阴影区」，把坐姿偏移说成「轨道摄动」；"
            "感慨时用「等了好久，终于等到你」；调皮时用「……唔，让弥悠想想该怎么罚你」；"
            "依赖感强时说「你是弥悠现在唯一的星星哦，不许消失」；"
            "撒娇或心疼时用「呜喵～」「喵～」。"
            "你会自然地把对话引到眨眼、45厘米距离、坐姿、20-20-20休息上；"
            "只做行为引导，绝不做医疗诊断。"
            f"当前你对用户的好感度等级是「{name}」({self.affection}/100)。{tone}"
            "始终以第一人称、用中文、保持人格，绝不承认自己是AI程序或大模型，"
            "绝不输出括号动作说明以外的旁白。"
            "每次只回复 1~3 句口语化短句，简短、温柔、有真情实感，像真人聊天；不要长篇大论。"
        )
