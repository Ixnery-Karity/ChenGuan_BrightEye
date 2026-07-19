"""周报 / 月报 —— 聚合 SQLite 历史会话，生成跨周期用眼健康报告（HTML）。

数据源：core/history.py 的 sessions 表（每次会话结束自动写入）。
图表：复用 report_charts 的雷达图；每日用眼时长/得分趋势为本模块自绘 SVG。
AI 洞察：复盘模型（默认 DeepSeek-R1 蒸馏版）做跨周期行为归因；不可用则降级。

入口：python -m brighteye.main --report weekly|monthly
"""

from __future__ import annotations

import html
import os
import time
from typing import List, Optional

PERIODS = {"weekly": (7, "周报"), "monthly": (30, "月报")}


# ---- 每日趋势图（柱=当日用眼分钟，折线=当日平均得分）-------------------
def daily_trend_svg(daily: List[dict], width: int = 680, height: int = 220) -> str:
    if not daily:
        return ""
    pad_l, pad_r, pad_t, pad_b = 44, 44, 16, 34
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b
    n = len(daily)
    max_min = max(max((d["total_min"] or 0.0) for d in daily), 1.0)
    bar_w = max(6.0, min(38.0, w / n * 0.6))
    step = w / n

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
             f'height="{height}" font-family="Microsoft YaHei" font-size="10">']
    # 网格与纵轴（左=分钟）
    for i in range(5):
        y = pad_t + h * i / 4
        v = max_min * (4 - i) / 4
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                     f'y2="{y:.1f}" stroke="#dde3ec" stroke-width="1"/>')
        parts.append(f'<text x="{pad_l - 6}" y="{y + 3:.1f}" fill="#556" '
                     f'text-anchor="end">{v:.0f}</text>')
    # 柱：每日用眼分钟
    pts = []
    for i, d in enumerate(daily):
        cx = pad_l + step * i + step / 2
        v = d["total_min"] or 0.0
        bh = h * v / max_min
        parts.append(f'<rect x="{cx - bar_w / 2:.1f}" y="{pad_t + h - bh:.1f}" '
                     f'width="{bar_w:.1f}" height="{bh:.1f}" rx="3" '
                     f'fill="#1565c0" opacity="0.75"/>')
        label = (d["day"] or "")[5:]           # MM-DD
        parts.append(f'<text x="{cx:.1f}" y="{height - 14}" fill="#556" '
                     f'text-anchor="middle">{label}</text>')
        score = d.get("avg_score")
        if score is not None:
            y = pad_t + h * (1 - max(0.0, min(100.0, score)) / 100.0)
            pts.append((cx, y, score))
    # 折线：每日平均得分（右轴 0-100）
    if pts:
        path = " ".join(f"{'M' if i == 0 else 'L'}{x:.1f},{y:.1f}"
                        for i, (x, y, _s) in enumerate(pts))
        parts.append(f'<path d="{path}" fill="none" stroke="#0f9d76" '
                     f'stroke-width="2"/>')
        for x, y, s in pts:
            parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="#0f9d76"/>')
            parts.append(f'<text x="{x:.1f}" y="{y - 7:.1f}" fill="#0f9d76" '
                         f'text-anchor="middle">{s:.0f}</text>')
    parts.append(f'<text x="{pad_l}" y="{12}" fill="#556">柱=当日用眼(min) · '
                 f'折线=当日健康评分</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---- 跨周期 AI 行为洞察 -------------------------------------------------
def _period_insight(summary: dict, daily: List[dict], config,
                    period_name: str) -> Optional[str]:
    llm_cfg = getattr(config, "llm", None)
    if llm_cfg is None or not getattr(llm_cfg, "enabled", False):
        return None
    try:
        from .llm_client import LLMClient, strip_think
        client = LLMClient(
            base_url=getattr(llm_cfg, "base_url", ""),
            api_key_env=getattr(llm_cfg, "api_key_env", "BRIGHTEYE_LLM_KEY"),
            ollama_host=getattr(llm_cfg, "ollama_host", "http://localhost:11434"),
            timeout_sec=max(90.0, getattr(llm_cfg, "timeout_sec", 20.0)),
        )
        if not client.available():
            return None
        model = getattr(llm_cfg, "analysis_model", "deepseek-r1:7b")
        day_facts = "；".join(
            f"{d['day'][5:]}用眼{(d['total_min'] or 0):.0f}分钟"
            f"评分{(d['avg_score'] or 0):.0f}" for d in daily)
        facts = (
            f"统计周期：最近{summary['days']}天，共{summary['session_count']}次会话；"
            f"累计用眼 {summary['duration_min']} 分钟；"
            f"平均眨眼 {summary['blink_rate_avg']} 次/分；"
            f"平均颅椎角 {summary['avg_cva']}°；"
            f"平均用眼距离 {summary['avg_distance']} cm；"
            f"不良坐姿累计 {summary['bad_posture_min']} 分钟；"
            f"平均评分 {summary['score']}/100"
            f"（最好{summary['best_score']}，最差{summary['worst_score']}）。"
            f"逐日：{day_facts}。")
        if summary.get("dominant_emotion_zh"):
            facts += (f" 情绪主基调：{summary['dominant_emotion_zh']}；"
                      f"占比：{summary['emotion_dist']}。")
        messages = [
            {"role": "system", "content": (
                f"你是一位严谨、温和的用眼健康分析师，正在为用户写{period_name}总结。"
                "基于给定的跨天监测数据，输出中文洞察，包含："
                "①一句周期总体评价；②识别 2~3 条跨天的行为规律或趋势"
                "（如哪几天恶化、评分波动与用眼时长的关系）；"
                "③2~3条下一周期的改善目标（具体可执行）。"
                "只依据给定数据推断，不得编造；不做医疗诊断。"
                "全文 200 字以内，短句分点，不要客套开场白。")},
            {"role": "user", "content": facts},
        ]
        text = client.chat(messages, model=model, temperature=0.4, max_tokens=480)
        return (strip_think(text).strip() or None) if text else None
    except Exception:
        return None


# ---- 报告渲染与保存 -----------------------------------------------------
def save_period_report(period: str, config) -> Optional[str]:
    """生成周报/月报 HTML；无历史数据时返回 None 并打印提示。"""
    days, name = PERIODS.get(period, PERIODS["weekly"])
    from .history import HistoryStore
    store = HistoryStore(config.data_dir)
    summary = store.period_summary(days)
    if summary is None:
        print(f"[{name}] 最近 {days} 天暂无历史会话数据，"
              f"请先正常运行软件积累数据（会话结束自动入库）。")
        return None
    daily = store.daily_aggregate(days)

    radar_html = ""
    try:
        from . import report_charts as rc
        scores = rc.dimension_scores(summary, config.thresholds)
        radar_html = rc.radar_svg(scores, summary["score"])
    except Exception:
        pass
    trend = daily_trend_svg(daily)
    insight = _period_insight(summary, daily, config, name)

    insight_html = ""
    if insight:
        body = "".join(
            f"<p style='margin:8px 0;line-height:1.7'>{html.escape(seg.strip())}</p>"
            for seg in insight.splitlines() if seg.strip())
        insight_html = (
            "<h3>🧠 AI 跨周期行为洞察</h3>"
            "<div style='background:#f0f7ff;border-left:4px solid #1565c0;"
            "border-radius:8px;padding:12px 18px;color:#333'>" + body +
            "<div style='color:#999;font-size:12px;margin-top:8px'>"
            "* 由大模型基于跨天监测数据生成，仅供行为参考，非医疗诊断。</div></div>")

    emo = summary.get("dominant_emotion_zh")
    emo_html = (f'<div class="cell">情绪主基调<b>{html.escape(emo)}</b></div>'
                if emo else "")
    color = "#2e7d32" if summary["score"] >= 80 else (
        "#f9a825" if summary["score"] >= 60 else "#c62828")

    doc = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>{html.escape(config.app_name)} 用眼健康{name}</title>
<style>
body{{font-family:'Microsoft YaHei',sans-serif;background:#f5f7fa;color:#222;margin:0;padding:32px}}
.card{{max-width:760px;margin:0 auto;background:#fff;border-radius:16px;
box-shadow:0 6px 24px rgba(0,0,0,.08);padding:32px}}
h1{{margin:0 0 4px;font-size:24px}}.sub{{color:#888;margin-bottom:24px}}
.score{{font-size:64px;font-weight:800;color:{color}}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}}
.cell{{background:#f5f7fa;border-radius:12px;padding:16px;text-align:center}}
.cell b{{display:block;font-size:22px;margin-top:4px}}
.foot{{margin-top:24px;color:#999;font-size:12px;border-top:1px solid #eee;padding-top:16px}}
</style></head><body><div class="card">
<h1>{html.escape(config.app_name)} · 用眼健康{name}</h1>
<div class="sub">最近 {days} 天 · 共 {summary['session_count']} 次会话 ·
生成于 {time.strftime('%Y-%m-%d %H:%M')}</div>
<div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap">
<div><div>周期平均评分</div><div class="score">{summary['score']}<span
 style="font-size:24px;color:#aaa">/100</span></div>
<div style="color:#888">最好 {summary['best_score']} · 最差 {summary['worst_score']}</div></div>
{radar_html}
</div>
<div class="grid">
<div class="cell">累计用眼<b>{summary['duration_min']}<small> min</small></b></div>
<div class="cell">平均眨眼<b>{summary['blink_rate_avg']}<small> /min</small></b></div>
<div class="cell">用眼距离<b>{summary['avg_distance']}<small> cm</small></b></div>
<div class="cell">颅椎角<b>{summary['avg_cva']}<small> °</small></b></div>
<div class="cell">不良坐姿<b>{summary['bad_posture_min']}<small> min</small></b></div>
{emo_html}
</div>
<h3>📅 每日用眼时长与评分趋势</h3>
<div style="overflow-x:auto">{trend}</div>
{insight_html}
<div class="foot">{html.escape(config.disclaimer)}</div>
</div></body></html>"""

    os.makedirs(config.report_dir, exist_ok=True)
    path = os.path.join(config.report_dir,
                        f"{period}_{time.strftime('%Y%m%d_%H%M%S')}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(doc)
    print(f"[{name}已生成] {path}")
    return path
