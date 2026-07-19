"""会话结束后生成健康报告（控制台文本 + HTML 文件）。

报告聚合本次会话的核心指标，给出综合用眼健康评分(0-100)与
分项建议，并附医疗合规免责声明。HTML 可直接用于答辩演示截图。
"""

from __future__ import annotations

import html
import os
import time
from typing import List

from .advice_engine import Advice, AdviceEngine, Level
from .metrics import SessionMetrics


def _score(metrics: SessionMetrics, thresholds) -> int:
    """综合用眼健康评分：从 100 分按不良指标扣分。"""
    score = 100.0
    br = metrics.blink_rate_avg()
    if br < thresholds.blink_rate_low:
        score -= 25
    elif br < thresholds.blink_rate_normal:
        score -= 12

    cva = metrics.avg_cva()
    if cva is not None:
        if cva < thresholds.cva_warning:
            score -= 25
        elif cva < thresholds.cva_good:
            score -= 12

    dist = metrics.avg_distance()
    if dist is not None and dist < thresholds.distance_min_cm:
        score -= 12

    # 不良坐姿/过近时长占比扣分
    bad_ratio = metrics.bad_posture_seconds / metrics.elapsed_sec
    score -= min(20.0, bad_ratio * 40.0)

    return max(0, int(round(score)))


def build_summary(metrics: SessionMetrics, thresholds,
                  other_device_min: float = 0.0) -> dict:
    from .emotion import EmotionEstimator
    dist = metrics.emotion_distribution()
    dom = metrics.dominant_emotion()
    return {
        "duration_min": round(metrics.elapsed_min, 1),
        "blink_total": metrics.blink_count,
        "blink_rate_avg": round(metrics.blink_rate_avg(), 1),
        "avg_cva": round(metrics.avg_cva(), 1) if metrics.avg_cva() else None,
        "avg_tilt": round(metrics.avg_tilt(), 1) if metrics.avg_tilt() else None,
        "avg_distance": round(metrics.avg_distance(), 1) if metrics.avg_distance() else None,
        "bad_posture_min": round(metrics.bad_posture_seconds / 60.0, 1),
        "too_close_min": round(metrics.too_close_seconds / 60.0, 1),
        "score": _score(metrics, thresholds),
        # 情绪时间线（供心理呵护叙事 + 大模型行为洞察）
        "dominant_emotion": dom,
        "dominant_emotion_zh": EmotionEstimator.zh(dom) if dom else None,
        "emotion_dist": {EmotionEstimator.zh(k): round(v * 100, 1) for k, v in dist.items()},
        # 多端同步（建议5）：其它设备今日上报的用眼分钟数，合并出全天候负荷
        "other_device_min": round(max(0.0, other_device_min), 1),
        "total_screen_min": round(metrics.elapsed_min + max(0.0, other_device_min), 1),
    }


def llm_insight(summary: dict, config) -> "str | None":
    """用大模型（复盘模型，默认 DeepSeek-R1 蒸馏版）对结构化指标做行为习惯洞察。

    输入是「已算好的客观指标」，让模型只做归因与个性化建议，不臆造数据。
    不可用/失败 → 返回 None，报告优雅降级为纯规则建议。
    """
    llm_cfg = getattr(config, "llm", None)
    if llm_cfg is None or not getattr(llm_cfg, "enabled", False):
        return None
    try:
        from .llm_client import LLMClient, strip_think
        client = LLMClient(
            base_url=getattr(llm_cfg, "base_url", ""),
            api_key_env=getattr(llm_cfg, "api_key_env", "BRIGHTEYE_LLM_KEY"),
            ollama_host=getattr(llm_cfg, "ollama_host", "http://localhost:11434"),
            # 复盘用推理模型(DeepSeek-R1)+首帧冷加载较慢，放宽超时(报告非实时、可等)
            timeout_sec=max(90.0, getattr(llm_cfg, "timeout_sec", 20.0)),
            auto_start=getattr(llm_cfg, "auto_start_ollama", True),
        )
        if not client.available():
            return None
        model = getattr(llm_cfg, "analysis_model", "deepseek-r1:7b")

        emo = summary.get("dominant_emotion_zh")
        emo_dist = summary.get("emotion_dist") or {}
        facts = (
            f"用眼时长 {summary['duration_min']} 分钟；"
            f"平均眨眼 {summary['blink_rate_avg']} 次/分；"
            f"平均颅椎角 {summary['avg_cva']}°(越大越直)；"
            f"平均高低肩 {summary['avg_tilt']}°；"
            f"平均用眼距离 {summary['avg_distance']} cm；"
            f"不良坐姿累计 {summary['bad_posture_min']} 分钟；"
            f"距离过近累计 {summary['too_close_min']} 分钟；"
            f"综合健康评分 {summary['score']}/100。"
        )
        if emo:
            facts += f" 情绪主基调：{emo}；情绪占比：{emo_dist}。"
        if summary.get("other_device_min", 0.0) > 0:
            facts += (
                f" 其它设备(手机等)今日另有用眼 {summary['other_device_min']} 分钟，"
                f"全天候合计 {summary['total_screen_min']} 分钟。")

        messages = [
            {"role": "system", "content": (
                "你是一位严谨、温和的用眼健康与行为习惯分析师。"
                "基于给定的客观监测指标，输出简洁的中文洞察，包含："
                "①一句总体评价；②2~3条最关键的行为习惯问题及其可能诱因；"
                "③2~3条具体、可执行的改善建议（含 20-20-20、用眼距离、坐姿、情绪调节）。"
                "只依据给定数据推断，不得编造未提供的数值；不做医疗诊断。"
                "全文控制在 180 字以内，用短句和分点，不要客套开场白。")},
            {"role": "user", "content": "本次会话监测数据：" + facts},
        ]
        text = client.chat(messages, model=model, temperature=0.4, max_tokens=420)
        if not text:
            return None
        return strip_think(text).strip() or None
    except Exception:
        return None


def final_advice(metrics: SessionMetrics, thresholds) -> List[Advice]:
    engine = AdviceEngine(thresholds)

    class _Rest:
        due_microbreak = False
        due_longbreak = metrics.elapsed_min >= thresholds.continuous_use_warn_min

    return engine.evaluate(
        metrics.blink_rate_avg(),
        metrics.avg_cva(),
        metrics.avg_tilt(),
        metrics.avg_distance(),
        _Rest(),
    )


def render_text(summary: dict, advices: List[Advice], config, insight: str = None,
                charts: dict = None) -> str:
    lines = [
        "=" * 52,
        f"  {config.app_name} · 用眼健康报告",
        "=" * 52,
        f"  本次用眼时长 : {summary['duration_min']} 分钟",
        f"  综合健康评分 : {summary['score']} / 100",
        "-" * 52,
        f"  眨眼总数     : {summary['blink_total']} 次",
        f"  平均眨眼频率 : {summary['blink_rate_avg']} 次/分",
        f"  平均颅椎角   : {summary['avg_cva']}°（越大越直）",
        f"  平均高低肩   : {summary['avg_tilt']}°",
        f"  平均用眼距离 : {summary['avg_distance']} cm",
        f"  不良坐姿累计 : {summary['bad_posture_min']} 分钟",
    ]
    if summary.get("dominant_emotion_zh"):
        lines.append(f"  情绪主基调   : {summary['dominant_emotion_zh']}")
    if summary.get("other_device_min", 0.0) > 0:
        lines.append(f"  其它设备用眼 : {summary['other_device_min']} 分钟"
                     f"（全天候合计 {summary['total_screen_min']} 分钟）")
    lines += ["-" * 52, "  个性化建议："]
    if advices:
        for a in advices:
            tag = {"info": "提示", "warn": "注意", "alert": "警示"}[a.level.value]
            lines.append(f"  [{tag}] {a.title} —— {a.detail}")
    else:
        lines.append("  本次用眼习惯良好，继续保持！")
    if charts:
        if charts.get("scores"):
            lines += ["-" * 52, "  分项得分（雷达图维度）："]
            lines.append("  " + "  ".join(
                f"{k} {v}" for k, v in charts["scores"].items()))
        if charts.get("risk_notes"):
            lines += ["-" * 52, "  ⚠ 风险时段标注："]
            for n in charts["risk_notes"]:
                lines.append("  · " + n)
        if charts.get("tips"):
            lines += ["-" * 52, "  🎯 针对性改善建议："]
            for tp in charts["tips"]:
                lines.append("  · " + tp)
    if insight:
        lines += ["-" * 52, "  🧠 AI 行为洞察："]
        for seg in insight.splitlines():
            seg = seg.strip()
            if seg:
                lines.append("  " + seg)
    lines += ["-" * 52, "  " + config.disclaimer, "=" * 52]
    return "\n".join(lines)


def render_html(summary: dict, advices: List[Advice], config, insight: str = None,
                charts: dict = None) -> str:
    color = "#2e7d32" if summary["score"] >= 80 else (
        "#f9a825" if summary["score"] >= 60 else "#c62828")
    items = ""
    for a in advices:
        badge = {"info": "#1565c0", "warn": "#f9a825", "alert": "#c62828"}[a.level.value]
        items += (
            f'<li><span style="background:{badge};color:#fff;padding:2px 8px;'
            f'border-radius:10px;font-size:12px">{html.escape(a.category)}</span> '
            f"<b>{html.escape(a.title)}</b><br><span style=\"color:#555\">"
            f"{html.escape(a.detail)}</span></li>"
        )
    if not items:
        items = "<li>本次用眼习惯良好，继续保持！</li>"

    # 🧠 AI 行为洞察段落（大模型生成；不可用时不显示，纯规则建议兜底）
    insight_html = ""
    if insight:
        body = "".join(
            f"<p style='margin:8px 0;line-height:1.7'>{html.escape(seg.strip())}</p>"
            for seg in insight.splitlines() if seg.strip()
        )
        insight_html = (
            "<h3>🧠 AI 行为洞察</h3>"
            "<div style='background:#f0f7ff;border-left:4px solid #1565c0;"
            "border-radius:8px;padding:12px 18px;color:#333'>" + body +
            "<div style='color:#999;font-size:12px;margin-top:8px'>"
            "* 由大模型基于本次监测指标生成，仅供行为参考，非医疗诊断。</div></div>"
        )

    # 情绪主基调徽标
    emo = summary.get("dominant_emotion_zh")
    emo_html = (f"<div class=\"cell\">情绪主基调<b>{html.escape(emo)}</b></div>"
                if emo else "")

    # —— 图表四件套（雷达图 / 趋势图 / 热力图 / 风险标注+针对性建议）——
    charts = charts or {}
    radar_html = ""
    if charts.get("radar"):
        radar_html = ("<div style='text-align:center'>" + charts["radar"] +
                      "<div style='color:#999;font-size:12px'>四维分项得分 · "
                      "综合评分居中</div></div>")
    trend_html = ""
    if charts.get("trend"):
        trend_html = ("<h3>📈 本次会话指标趋势</h3>"
                      "<div style='overflow-x:auto'>" + charts["trend"] + "</div>")
    heat_html = ""
    if charts.get("heatmap"):
        notes = "".join(
            f"<li style='color:#b23;margin:6px 0'>⚠ {html.escape(n)}</li>"
            for n in charts.get("risk_notes", []))
        heat_html = ("<h3>🕒 分时段用眼负荷（红=不良用眼占比高）</h3>"
                     + charts["heatmap"]
                     + (f"<ul style='list-style:none;padding:0'>{notes}</ul>"
                        if notes else ""))
    tips_html = ""
    if charts.get("tips"):
        tips_html = ("<h3>🎯 针对性改善建议</h3><ul>"
                     + "".join(f"<li>{html.escape(tp)}</li>"
                               for tp in charts["tips"]) + "</ul>")

    # 多端同步：其它设备今日用眼（>0 才显示）
    sync_html = ""
    if summary.get("other_device_min", 0.0) > 0:
        sync_html = (
            f"<div class=\"cell\">📱 其它设备<b>{summary['other_device_min']}"
            f"<small> min</small></b></div>"
            f"<div class=\"cell\">全天候合计<b>{summary['total_screen_min']}"
            f"<small> min</small></b></div>")

    return f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>{html.escape(config.app_name)} 用眼健康报告</title>
<style>
body{{font-family:'Microsoft YaHei',sans-serif;background:#f5f7fa;color:#222;margin:0;padding:32px}}
.card{{max-width:760px;margin:0 auto;background:#fff;border-radius:16px;
box-shadow:0 6px 24px rgba(0,0,0,.08);padding:32px}}
h1{{margin:0 0 4px;font-size:24px}}.sub{{color:#888;margin-bottom:24px}}
.score{{font-size:64px;font-weight:800;color:{color}}}
.grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:16px;margin:24px 0}}
.cell{{background:#f5f7fa;border-radius:12px;padding:16px;text-align:center}}
.cell b{{display:block;font-size:22px;margin-top:4px}}
ul{{list-style:none;padding:0}}li{{margin:14px 0;line-height:1.6}}
.foot{{margin-top:24px;color:#999;font-size:12px;border-top:1px solid #eee;padding-top:16px}}
</style></head><body><div class="card">
<h1>{html.escape(config.app_name)} · 用眼健康报告</h1>
<div class="sub">{html.escape(config.subtitle)} · 生成于 {time.strftime('%Y-%m-%d %H:%M')}</div>
<div style="display:flex;align-items:center;gap:24px;flex-wrap:wrap">
<div><div>综合健康评分</div><div class="score">{summary['score']}<span style="font-size:24px;color:#aaa">/100</span></div></div>
{radar_html}
</div>
<div class="grid">
<div class="cell">用眼时长<b>{summary['duration_min']}<small> min</small></b></div>
<div class="cell">平均眨眼<b>{summary['blink_rate_avg']}<small> /min</small></b></div>
<div class="cell">用眼距离<b>{summary['avg_distance']}<small> cm</small></b></div>
<div class="cell">颅椎角<b>{summary['avg_cva']}<small> °</small></b></div>
<div class="cell">高低肩<b>{summary['avg_tilt']}<small> °</small></b></div>
<div class="cell">不良坐姿<b>{summary['bad_posture_min']}<small> min</small></b></div>
{emo_html}
{sync_html}
</div>
{trend_html}
{heat_html}
<h3>个性化建议</h3><ul>{items}</ul>
{tips_html}
{insight_html}
<div class="foot">{html.escape(config.disclaimer)}</div>
</div></body></html>"""


def save_report(metrics: SessionMetrics, config) -> str:
    # 多端同步开启时，把其它设备今日上报的用眼分钟数并入报告
    other_min = 0.0
    if getattr(getattr(config, "sync", None), "enabled", False):
        try:
            from .sync import SyncStore
            other_min = SyncStore(config.data_dir).total_other_min()
        except Exception:
            other_min = 0.0
    summary = build_summary(metrics, config.thresholds, other_device_min=other_min)
    # —— 历史持久化：会话指标写入本地 SQLite，供周报/月报聚合（失败静默）——
    try:
        from .history import HistoryStore
        HistoryStore(config.data_dir).save_session(summary)
    except Exception:
        pass
    advices = final_advice(metrics, config.thresholds)
    insight = llm_insight(summary, config)   # 大模型行为洞察（不可用则 None，降级纯规则）

    # —— 图表四件套：雷达 / 趋势 / 热力 / 风险标注 + 针对性建议（纯SVG零依赖）——
    charts = {}
    try:
        from . import report_charts as rc
        scores = rc.dimension_scores(summary, config.thresholds)
        segs = metrics.finish_segments()
        charts = {
            "scores": scores,
            "radar": rc.radar_svg(scores, summary["score"]),
            "trend": rc.trend_svg(metrics.timeline),
            "heatmap": rc.heatmap_svg(metrics.hour_load),
            "risk_notes": rc.risk_notes(segs, metrics.hour_load, config.thresholds),
            "tips": rc.personalized_tips(summary, scores, config.thresholds),
        }
    except Exception:
        charts = {}   # 图表失败不影响报告主体（铁律：优雅降级）

    os.makedirs(config.report_dir, exist_ok=True)
    path = os.path.join(
        config.report_dir, f"report_{time.strftime('%Y%m%d_%H%M%S')}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render_html(summary, advices, config, insight, charts))
    print(render_text(summary, advices, config, insight, charts))
    return path
