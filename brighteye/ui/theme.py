"""仪表盘双主题系统（v1.12.0）—— 配色 token 集中管理，🎨 一键切换。

两套官方主题（均按弥悠人设的粉紫发/紫瞳定调，调研自 Anime Spectacle /
Neo-Kawaii Tech 两类风格）：
  · miyou_night「弥悠·星夜」：午夜紫深底 + 发光描边 + 高饱和紫/青/粉，
    保留电竞导播骨架的科技感（默认）；
  · miyou_candy「弥悠·奶糖」：奶白粉紫浅底 + 糖果色卡片 + 贴纸式描边，
    可爱风；文字颜色加深保证对比度（浅底可读性）。

主题选择持久化到 data/ui_theme.json（纯标准库，失败静默用默认）。
tkinter 无真实毛玻璃模糊，两套主题均用「纯色叠层 + 描边」模拟层次。
"""

from __future__ import annotations

import json
import os
from typing import Dict

DEFAULT_THEME = "miyou_night"

# token 说明：bg 窗口底 / panel 卡片 / panel2 次级条 / teal 主色(健康、品牌) /
# cyan 辅助 / coral 告警强调 / amber 次告警 / fg 正文 / muted 弱化文字
THEMES: Dict[str, Dict[str, str]] = {
    "miyou_night": {
        "label": "弥悠·星夜",
        "icon": "🌙",
        "bg": "#0B0620",
        "panel": "#17102F",
        "panel2": "#221741",
        "teal": "#8E6BFF",     # 主色：弥悠瞳色紫
        "cyan": "#1FD6FF",
        "coral": "#FF4FB4",    # 告警：品牌粉
        "amber": "#FFC94D",
        "fg": "#F2EDFF",
        "muted": "#8F86B8",
    },
    "miyou_candy": {
        "label": "弥悠·奶糖",
        "icon": "🍬",
        "bg": "#FFF7FD",
        "panel": "#FFFFFF",
        "panel2": "#F4E9FA",
        "teal": "#7E7BFF",     # 主色：糖果紫
        "cyan": "#3FA8CC",     # 辅助青（加深保证浅底对比度）
        "coral": "#E8438A",    # 告警粉（加深）
        "amber": "#D98A1F",    # 次告警（加深）
        "fg": "#43355C",       # 深紫灰正文
        "muted": "#9C8FB8",
    },
}

_ORDER = ["miyou_night", "miyou_candy"]

# 强制休息遮罩固定用暗色（休息时理应调暗环境，与主题无关）
GUARD = {
    "bg": "#060910",
    "fg": "#EAF0FF",
    "muted": "#8A93B5",
    "teal": "#2EE6A6",
    "amber": "#FFC94D",
    "coral": "#FF5277",
    "panel2": "#1A2540",
}


def _pref_path(data_dir: str) -> str:
    return os.path.join(data_dir, "ui_theme.json")


def load_theme_name(data_dir: str = "data") -> str:
    try:
        with open(_pref_path(data_dir), "r", encoding="utf-8") as f:
            name = json.load(f).get("theme", "")
        if name in THEMES:
            return name
    except Exception:
        pass
    return DEFAULT_THEME


def save_theme_name(name: str, data_dir: str = "data") -> None:
    try:
        os.makedirs(data_dir, exist_ok=True)
        with open(_pref_path(data_dir), "w", encoding="utf-8") as f:
            json.dump({"theme": name}, f, ensure_ascii=False)
    except Exception:
        pass


def next_theme_name(current: str) -> str:
    try:
        i = _ORDER.index(current)
    except ValueError:
        i = 0
    return _ORDER[(i + 1) % len(_ORDER)]


def get_theme(name: str) -> Dict[str, str]:
    return THEMES.get(name, THEMES[DEFAULT_THEME])
