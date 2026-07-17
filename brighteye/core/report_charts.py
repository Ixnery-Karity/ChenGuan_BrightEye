"""报告图表（纯标准库生成内联 SVG，零依赖 · 建议3「report 改进」落地）。

四件套：
  1. radar_svg      —— 四维雷达图（眨眼/距离/坐姿/时长 分项得分，综合分居中）
  2. trend_svg      —— 本次会话指标趋势图（眨眼率/距离/颅椎角 时间序列）
  3. heatmap_svg    —— 分时段风险热力图（一天 24 小时用眼负荷，红=不良占比高）
  4. risk_notes     —— 风险时段文字标注（如「15:00-16:20 连续用眼 80 分钟无休息」）

全部输出 SVG 字符串直接内联进 HTML 报告，无 matplotlib/plotly 等依赖，
任何浏览器可渲染，符合离线可演示铁律。
"""

from __future__ import annotations

import math
import time
from typing import List, Optional, Tuple

# 与报告页一致的浅色系配色
_C_TEAL = "#0f9d76"
_C_CYAN = "#1565c0"
_C_AMBER = "#f9a825"
_C_CORAL = "#c62828"
_C_GRID = "#dde3ec"
_C_TXT = "#556"


# ---------------------------------------------------------------- 分项得分
def dimension_scores(summary: dict, thresholds) -> dict:
    """把会话指标折算为四个维度的百分制分项得分（雷达图轴）。"""
    def clamp(v):
        return max(0, min(100, int(round(v))))

    # 眨眼：正常≥15 满分，10 以下线性掉到 30
    br = summary.get("blink_rate_avg") or 0.0
    blink = clamp(30 + 70 * min(1.0, br / thresholds.blink_rate_normal))

    # 距离：理想 55cm 满分，低于 45 明显扣分
    d = summary.get("avg_distance")
    if d is None:
        dist = 60
    else:
        dist = clamp(100 - max(0.0, thresholds.distance_ideal_cm - d) * 4.0)

    # 坐姿：CVA≥50 满分；再按不良坐姿时长占比扣
    cva = summary.get("avg_cva")
    posture = 60 if cva is None else clamp(
        100 - max(0.0, thresholds.cva_good - cva) * 5.0)
    dur = max(0.1, summary.get("duration_min") or 0.1)
    posture = clamp(posture - (summary.get("bad_posture_min", 0.0) / dur) * 30)

    # 时长节律：连续用眼越接近告警阈值越低（含跨设备负荷）
    total = summary.get("total_screen_min") or summary.get("duration_min") or 0.0
    rhythm = clamp(100 - max(0.0, total - thresholds.break_interval_min) * 1.2)

    return {"眨眼健康": blink, "用眼距离": dist, "坐姿体态": posture, "时长节律": rhythm}


# ---------------------------------------------------------------- 1. 雷达图
def radar_svg(scores: dict, overall: int, size: int = 300) -> str:
    """四维雷达图；overall 综合分显示在中心。"""
    cx = cy = size / 2
    r_max = size / 2 - 46
    labels = list(scores.keys())
    vals = [scores[k] / 100.0 for k in labels]
    n = len(labels)

    def pt(idx: int, ratio: float) -> Tuple[float, float]:
        ang = -math.pi / 2 + idx * 2 * math.pi / n
        return (cx + r_max * ratio * math.cos(ang),
                cy + r_max * ratio * math.sin(ang))

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" '
             f'height="{size}" viewBox="0 0 {size} {size}">']
    # 网格（同心多边形）
    for g in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, g) for i in range(n)))
        parts.append(f'<polygon points="{pts}" fill="none" stroke="{_C_GRID}" '
                     f'stroke-width="1"/>')
    for i in range(n):  # 轴线
        x, y = pt(i, 1.0)
        parts.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.1f}" y2="{y:.1f}" '
                     f'stroke="{_C_GRID}" stroke-width="1"/>')
    # 数据多边形
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in (pt(i, vals[i]) for i in range(n)))
    parts.append(f'<polygon points="{pts}" fill="{_C_TEAL}" fill-opacity="0.25" '
                 f'stroke="{_C_TEAL}" stroke-width="2"/>')
    for i in range(n):  # 顶点 + 标签 + 分值
        x, y = pt(i, vals[i])
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3" fill="{_C_TEAL}"/>')
        lx, ly = pt(i, 1.22)
        anchor = "middle"
        if lx > cx + 8:
            anchor = "start"
        elif lx < cx - 8:
            anchor = "end"
        parts.append(f'<text x="{lx:.1f}" y="{ly:.1f}" font-size="12" fill="{_C_TXT}" '
                     f'text-anchor="{anchor}">{labels[i]} {int(scores[labels[i]])}</text>')
    # 中心综合分
    color = _C_TEAL if overall >= 80 else (_C_AMBER if overall >= 60 else _C_CORAL)
    parts.append(f'<text x="{cx}" y="{cy + 2}" font-size="26" font-weight="bold" '
                 f'fill="{color}" text-anchor="middle">{overall}</text>')
    parts.append(f'<text x="{cx}" y="{cy + 18}" font-size="10" fill="#999" '
                 f'text-anchor="middle">综合评分</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- 2. 趋势图
def trend_svg(timeline: List[tuple], width: int = 680, height: int = 220) -> str:
    """三条时间序列折线：眨眼率(次/分)、距离(cm)、颅椎角(°)。

    timeline: [(ts, blink_rate, distance, cva), ...]（10s 采样）。
    各序列独立归一化到画布高度，右侧图例标注色。点太少(<3)返回空串。
    """
    pts = [p for p in timeline if p]
    if len(pts) < 3:
        return ""
    pad_l, pad_r, pad_t, pad_b = 40, 14, 16, 26
    w = width - pad_l - pad_r
    h = height - pad_t - pad_b
    t0, t1 = pts[0][0], pts[-1][0]
    span = max(1.0, t1 - t0)

    series = [  # (index, label, color)
        (1, "眨眼 次/分", _C_TEAL),
        (2, "距离 cm", _C_CYAN),
        (3, "颅椎角 °", _C_AMBER),
    ]
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
             f'height="{height}" viewBox="0 0 {width} {height}">']
    # 网格横线
    for gy in range(5):
        y = pad_t + h * gy / 4
        parts.append(f'<line x1="{pad_l}" y1="{y:.1f}" x2="{width - pad_r}" '
                     f'y2="{y:.1f}" stroke="{_C_GRID}" stroke-width="1"/>')
    # 时间轴标签（起/中/末，时:分）
    for ratio in (0.0, 0.5, 1.0):
        ts = t0 + span * ratio
        x = pad_l + w * ratio
        label = time.strftime("%H:%M", time.localtime(ts))
        parts.append(f'<text x="{x:.1f}" y="{height - 8}" font-size="10" '
                     f'fill="#999" text-anchor="middle">{label}</text>')

    for idx, label, color in series:
        vals = [(p[0], p[idx]) for p in pts if p[idx] is not None]
        if len(vals) < 2:
            continue
        vmin = min(v for _, v in vals)
        vmax = max(v for _, v in vals)
        vspan = max(1e-6, vmax - vmin)
        path = []
        for ts, v in vals:
            x = pad_l + w * (ts - t0) / span
            y = pad_t + h * (1 - (v - vmin) / vspan)
            path.append(f"{x:.1f},{y:.1f}")
        parts.append(f'<polyline points="{" ".join(path)}" fill="none" '
                     f'stroke="{color}" stroke-width="1.8" '
                     f'stroke-linejoin="round"/>')
        # 序列末值标注
        x_end, y_end = path[-1].split(",")
        parts.append(f'<text x="{float(x_end) + 3:.1f}" y="{y_end}" font-size="9" '
                     f'fill="{color}">{vals[-1][1]:.0f}</text>')
    # 图例
    lx = pad_l
    for _, label, color in series:
        parts.append(f'<rect x="{lx}" y="2" width="10" height="3" fill="{color}"/>')
        parts.append(f'<text x="{lx + 14}" y="7" font-size="10" '
                     f'fill="{_C_TXT}">{label}</text>')
        lx += 14 + 9 * len(label) + 14
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- 3. 热力图
def heatmap_svg(hour_load: dict, width: int = 680, height: int = 96) -> str:
    """24 小时用眼负荷热力条：格子越红代表该小时不良用眼占比越高。

    hour_load: {hour: {"use": 秒, "bad": 秒}}。无数据小时画浅灰格。
    """
    if not hour_load:
        return ""
    pad, label_h = 4, 26
    cell_w = (width - pad * 2) / 24
    cell_h = height - label_h - pad
    max_use = max((r["use"] for r in hour_load.values()), default=1.0) or 1.0

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
             f'height="{height}" viewBox="0 0 {width} {height}">']
    for hh in range(24):
        x = pad + hh * cell_w
        rec = hour_load.get(hh)
        if not rec or rec["use"] < 30:          # <30s 视为无数据
            fill, opacity = "#eef1f6", 1.0
        else:
            bad_ratio = min(1.0, rec["bad"] / max(1.0, rec["use"]))
            # 用眼强度决定透明度，不良占比决定 绿→红 渐变
            r = int(15 + (198 - 15) * bad_ratio)
            g = int(157 - (157 - 40) * bad_ratio)
            b = int(118 - (118 - 40) * bad_ratio)
            fill = f"#{r:02x}{g:02x}{b:02x}"
            opacity = 0.35 + 0.65 * min(1.0, rec["use"] / max_use)
        parts.append(f'<rect x="{x:.1f}" y="{pad}" width="{cell_w - 2:.1f}" '
                     f'height="{cell_h:.1f}" rx="3" fill="{fill}" '
                     f'fill-opacity="{opacity:.2f}"/>')
        if hh % 3 == 0:
            parts.append(f'<text x="{x + cell_w / 2:.1f}" y="{height - 8}" '
                         f'font-size="9" fill="#999" '
                         f'text-anchor="middle">{hh:02d}</text>')
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------- 4. 风险标注
def risk_notes(segments: List[tuple], hour_load: dict, thresholds,
               limit: int = 3) -> List[str]:
    """产出风险时段文字标注，如「15:00-16:20 连续用眼 80 分钟无休息」。"""
    notes: List[str] = []
    warn_min = thresholds.continuous_use_warn_min
    # 连续用眼超阈值的段（取最长的几段）
    long_segs = sorted(
        ((a, b) for a, b in segments if (b - a) / 60.0 >= warn_min),
        key=lambda s: s[1] - s[0], reverse=True)
    for a, b in long_segs[:limit]:
        notes.append(
            f"{time.strftime('%H:%M', time.localtime(a))}"
            f"-{time.strftime('%H:%M', time.localtime(b))} "
            f"连续用眼 {int((b - a) / 60)} 分钟无休息")
    # 不良占比最高的小时（>25% 且用眼超 10 分钟才提）
    worst = None
    for hh, rec in hour_load.items():
        if rec["use"] >= 600 and rec["bad"] / rec["use"] > 0.25:
            if worst is None or rec["bad"] / rec["use"] > worst[1]:
                worst = (hh, rec["bad"] / rec["use"])
    if worst:
        notes.append(f"{worst[0]:02d}:00-{worst[0] + 1:02d}:00 时段不良用眼"
                     f"（低头/过近）占比 {worst[1] * 100:.0f}%，为全天风险高峰")
    return notes[:limit + 1]


# ---------------------------------------------------------------- 5. 个性化建议
def personalized_tips(summary: dict, scores: dict, thresholds) -> List[str]:
    """按最薄弱维度产出针对性改善建议（规则版，始终可用；LLM 洞察另叙）。"""
    tips: List[str] = []
    if scores["眨眼健康"] < 70:
        tips.append(f"你的眨眼频率偏低（{summary.get('blink_rate_avg')} 次/分，"
                    f"正常约 15-20），建议有意识地多眨眼，或屏幕旁贴「眨眼」便利贴提醒自己。")
    if scores["用眼距离"] < 70 and summary.get("avg_distance"):
        tips.append(f"平均用眼距离 {summary['avg_distance']} cm 偏近，"
                    f"建议保持 {thresholds.distance_ideal_cm:.0f} cm 以上——"
                    f"约一臂距离，可把屏幕调远或增大字体。")
    if scores["坐姿体态"] < 70:
        tips.append("颅椎角偏小说明头部前倾明显，建议把显示器上沿抬至与视线平齐，"
                    "配合靠背支撑腰部，每 30 分钟活动颈肩。")
    if scores["时长节律"] < 70:
        tips.append("连续用眼时间偏长，请遵循 20-20-20 法则：每 20 分钟"
                    "远眺 6 米外 20 秒；久坐 45 分钟起身活动。")
    if summary.get("other_device_min", 0.0) > 30:
        tips.append(f"注意：今天其它设备（手机等）另有 {summary['other_device_min']}"
                    f" 分钟用眼——离开电脑去玩手机，眼睛并没有休息。")
    if not tips:
        tips.append("四个维度均表现良好，继续保持当前用眼节奏即可。")
    return tips
