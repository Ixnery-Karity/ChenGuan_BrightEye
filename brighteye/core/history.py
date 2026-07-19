"""历史持久化 —— SQLite 本地存储（纯标准库 sqlite3，零新增依赖）。

三张表，各司其职：
  · sessions      每次会话结束时写入一行指标汇总 → 周报/月报的数据源；
  · affection     好感度与累计对话轮数的单行状态 → 跨会话延续养成进度；
  · chat_events   好感度变化的关键对话事件（中期记忆层）→ 聊天时可回注上下文。

铁律：任何数据库异常都被吞掉并安全降级（返回空/默认值），绝不影响演示。
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import List, Optional


class HistoryStore:
    """本地历史库。用完即关，不持有长连接（tkinter 多线程安全考虑）。"""

    def __init__(self, data_dir: str = "data"):
        self.db_path = os.path.join(data_dir, "history.db")
        try:
            os.makedirs(data_dir, exist_ok=True)
            self._init_db()
            self.ok = True
        except Exception:
            self.ok = False

    def _conn(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=5.0)
        con.row_factory = sqlite3.Row
        return con

    def _init_db(self) -> None:
        with self._conn() as con:
            con.executescript("""
            CREATE TABLE IF NOT EXISTS sessions(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                day TEXT NOT NULL,
                duration_min REAL, blink_rate_avg REAL,
                avg_cva REAL, avg_tilt REAL, avg_distance REAL,
                bad_posture_min REAL, too_close_min REAL,
                score INTEGER,
                dominant_emotion TEXT, emotion_dist TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_day ON sessions(day);
            CREATE TABLE IF NOT EXISTS affection(
                id INTEGER PRIMARY KEY CHECK(id=1),
                value INTEGER NOT NULL,
                total_turns INTEGER NOT NULL DEFAULT 0,
                daily_gain INTEGER NOT NULL DEFAULT 0,
                daily_day TEXT NOT NULL DEFAULT '',
                updated_ts REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_events(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                user_text TEXT, reply_text TEXT,
                category TEXT, delta INTEGER, affection INTEGER
            );
            """)

    # ---- 会话历史（周报/月报数据源）----------------------------------
    def save_session(self, summary: dict) -> bool:
        """会话结束时写入一行指标汇总；失败返回 False（不抛异常）。"""
        if not self.ok:
            return False
        try:
            now = time.time()
            with self._conn() as con:
                con.execute(
                    "INSERT INTO sessions(ts, day, duration_min, blink_rate_avg,"
                    " avg_cva, avg_tilt, avg_distance, bad_posture_min,"
                    " too_close_min, score, dominant_emotion, emotion_dist)"
                    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now, time.strftime("%Y-%m-%d", time.localtime(now)),
                     summary.get("duration_min"), summary.get("blink_rate_avg"),
                     summary.get("avg_cva"), summary.get("avg_tilt"),
                     summary.get("avg_distance"), summary.get("bad_posture_min"),
                     summary.get("too_close_min"), summary.get("score"),
                     summary.get("dominant_emotion"),
                     json.dumps(summary.get("emotion_dist") or {},
                                ensure_ascii=False)))
            return True
        except Exception:
            return False

    def recent_sessions(self, days: int) -> List[dict]:
        """最近 N 天的全部会话行（升序）。"""
        if not self.ok:
            return []
        try:
            since = time.time() - days * 86400
            with self._conn() as con:
                rows = con.execute(
                    "SELECT * FROM sessions WHERE ts >= ? ORDER BY ts ASC",
                    (since,)).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def daily_aggregate(self, days: int) -> List[dict]:
        """按天聚合最近 N 天：每天的用眼分钟数 / 平均分 / 会话数等（升序）。"""
        if not self.ok:
            return []
        try:
            since = time.time() - days * 86400
            with self._conn() as con:
                rows = con.execute(
                    "SELECT day, COUNT(*) AS sessions,"
                    " SUM(duration_min) AS total_min,"
                    " AVG(score) AS avg_score,"
                    " AVG(blink_rate_avg) AS blink_rate_avg,"
                    " AVG(avg_cva) AS avg_cva,"
                    " AVG(avg_distance) AS avg_distance,"
                    " SUM(bad_posture_min) AS bad_posture_min,"
                    " SUM(too_close_min) AS too_close_min"
                    " FROM sessions WHERE ts >= ? GROUP BY day ORDER BY day ASC",
                    (since,)).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def period_summary(self, days: int) -> Optional[dict]:
        """整周期汇总（周报=7 / 月报=30），键与单次会话 summary 对齐，
        便于直接复用 report_charts.dimension_scores / radar_svg。"""
        rows = self.recent_sessions(days)
        if not rows:
            return None

        def _avg(key):
            vals = [r[key] for r in rows if r.get(key) is not None]
            return round(sum(vals) / len(vals), 1) if vals else None

        def _sum(key):
            return round(sum(r.get(key) or 0.0 for r in rows), 1)

        # 情绪占比合并（各会话占比按时长加权平均）
        emo_acc, w_acc = {}, 0.0
        for r in rows:
            try:
                dist = json.loads(r.get("emotion_dist") or "{}")
            except Exception:
                dist = {}
            w = r.get("duration_min") or 0.0
            for k, v in dist.items():
                emo_acc[k] = emo_acc.get(k, 0.0) + v * w
            w_acc += w
        emo_dist = ({k: round(v / w_acc, 1) for k, v in emo_acc.items()}
                    if w_acc > 0 else {})
        dominant = max(emo_dist, key=emo_dist.get) if emo_dist else None

        scores = [r["score"] for r in rows if r.get("score") is not None]
        return {
            "days": days,
            "session_count": len(rows),
            "duration_min": _sum("duration_min"),
            "blink_rate_avg": _avg("blink_rate_avg") or 0.0,
            "avg_cva": _avg("avg_cva"),
            "avg_tilt": _avg("avg_tilt"),
            "avg_distance": _avg("avg_distance"),
            "bad_posture_min": _sum("bad_posture_min"),
            "too_close_min": _sum("too_close_min"),
            "score": int(round(sum(scores) / len(scores))) if scores else 0,
            "best_score": max(scores) if scores else 0,
            "worst_score": min(scores) if scores else 0,
            "dominant_emotion_zh": dominant,
            "emotion_dist": emo_dist,
        }

    # ---- 好感度状态（跨会话延续养成进度）------------------------------
    def load_affection(self) -> Optional[dict]:
        """读取好感度状态；无记录返回 None（首次运行用默认值）。"""
        if not self.ok:
            return None
        try:
            with self._conn() as con:
                row = con.execute(
                    "SELECT * FROM affection WHERE id=1").fetchone()
            return dict(row) if row else None
        except Exception:
            return None

    def save_affection(self, value: int, total_turns: int,
                       daily_gain: int, daily_day: str) -> None:
        if not self.ok:
            return
        try:
            with self._conn() as con:
                con.execute(
                    "INSERT INTO affection(id, value, total_turns, daily_gain,"
                    " daily_day, updated_ts) VALUES(1,?,?,?,?,?)"
                    " ON CONFLICT(id) DO UPDATE SET value=excluded.value,"
                    " total_turns=excluded.total_turns,"
                    " daily_gain=excluded.daily_gain,"
                    " daily_day=excluded.daily_day,"
                    " updated_ts=excluded.updated_ts",
                    (value, total_turns, daily_gain, daily_day, time.time()))
        except Exception:
            pass

    # ---- 关键对话事件（中期记忆层）------------------------------------
    def log_chat_event(self, user_text: str, reply_text: str,
                       category: Optional[str], delta: int,
                       affection: int) -> None:
        """记录好感度发生变化的关键对话（|delta|>=3 的强情绪事件才值得记）。"""
        if not self.ok:
            return
        try:
            with self._conn() as con:
                con.execute(
                    "INSERT INTO chat_events(ts, user_text, reply_text,"
                    " category, delta, affection) VALUES(?,?,?,?,?,?)",
                    (time.time(), user_text[:200], reply_text[:200],
                     category, delta, affection))
                # 只保留最近 200 条，防库膨胀
                con.execute(
                    "DELETE FROM chat_events WHERE id NOT IN"
                    " (SELECT id FROM chat_events ORDER BY id DESC LIMIT 200)")
        except Exception:
            pass

    def recent_chat_events(self, limit: int = 5) -> List[dict]:
        """最近的关键对话事件（倒序取、正序返回），供聊天引擎回注中期记忆。"""
        if not self.ok:
            return []
        try:
            with self._conn() as con:
                rows = con.execute(
                    "SELECT * FROM chat_events ORDER BY id DESC LIMIT ?",
                    (limit,)).fetchall()
            return [dict(r) for r in reversed(rows)]
        except Exception:
            return []
