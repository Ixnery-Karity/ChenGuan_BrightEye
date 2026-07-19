"""一键打包脚本（建议2落地）：PyInstaller onedir + Inno Setup 模板。

用法（在 challenge 目录下）：
    pip install pyinstaller
    python -m brighteye.tools.build_exe            # 生成 dist/宸观BrightEye/
    python -m brighteye.tools.build_exe --iss-only # 只生成 Inno Setup 脚本

产物：
  dist/宸观BrightEye/宸观BrightEye.exe   —— 免 Python 环境直接运行
  build_installer.iss                    —— Inno Setup 安装包脚本(可选，
                                            装 Inno Setup 6 后编译出 setup.exe)

说明：
  · onedir 模式（非 onefile）：mediapipe 含大量动态库，onedir 启动快且稳；
  · --collect-all mediapipe：打进 .task 模型运行时与二进制依赖；
  · assets/ 整体随包（模型/立绘）；
  · 软件图标（v1.12.0）：把设计好的图标放到 brighteye/assets/app_icon.ico，
    或放 app_icon.png（已装 Pillow 时自动转多尺寸 .ico：16~256）；
    自动接入 exe 图标与 Inno 安装向导图标，缺失则用默认图标不报错；
  · 本脚本仅生成产物，不修改源码；PyInstaller 未安装时给出友好提示。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys

APP_NAME = "宸观BrightEye"
ENTRY = "brighteye/main.py"

# 本文件位于 brighteye/tools/，项目根 = 上上级目录
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

ISS_TEMPLATE = r"""; 宸观 BrightEye · Inno Setup 安装包脚本（由 build_exe.py 生成）
; 编译方式：安装 Inno Setup 6 (https://jrsoftware.org/isinfo.php)
;           右键本文件 → Compile，产出 Output\宸观BrightEye_Setup.exe
[Setup]
AppName=宸观 BrightEye
AppVersion={version}
AppPublisher=宸观科技团队
DefaultDirName={{autopf}}\ChenguanBrightEye
DefaultGroupName=宸观 BrightEye
UninstallDisplayIcon={{app}}\{app_name}.exe
OutputBaseFilename={app_name}_Setup_v{version}
{setup_icon_line}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\{app_name}\*"; DestDir: "{{app}}"; Flags: recursesubdirs

[Icons]
Name: "{{group}}\宸观 BrightEye"; Filename: "{{app}}\{app_name}.exe"
Name: "{{autodesktop}}\宸观 BrightEye"; Filename: "{{app}}\{app_name}.exe"

[Run]
Filename: "{{app}}\{app_name}.exe"; Description: "立即启动 宸观 BrightEye"; \
    Flags: nowait postinstall skipifsilent
"""


def _version() -> str:
    sys.path.insert(0, ROOT)
    from brighteye.config import CONFIG
    return CONFIG.version


def prepare_icon() -> str:
    """定位软件图标：assets/app_icon.ico 直接用；只有 PNG 时尝试 Pillow 转
    多尺寸 .ico（16~256，Windows 任务栏/桌面/资源管理器全覆盖）。找不到返回空串。"""
    assets = os.path.join(ROOT, "brighteye", "assets")
    ico = os.path.join(assets, "app_icon.ico")
    if os.path.isfile(ico):
        return ico
    png = os.path.join(assets, "app_icon.png")
    if os.path.isfile(png):
        try:
            from PIL import Image
            img = Image.open(png).convert("RGBA")
            img.save(ico, format="ICO",
                     sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                            (64, 64), (128, 128), (256, 256)])
            print(f"[图标] 已由 PNG 生成多尺寸 ICO：{ico}")
            return ico
        except ImportError:
            print("[提示] 检测到 app_icon.png 但未装 Pillow（pip install pillow），"
                  "本次打包用默认图标")
        except Exception as e:
            print(f"[提示] PNG→ICO 转换失败({e})，本次打包用默认图标")
    return ""


def write_iss(version: str, icon_path: str = "") -> str:
    path = os.path.join(ROOT, "build_installer.iss")
    line = f"SetupIconFile={icon_path}" if icon_path else "; SetupIconFile=(未配置图标)"
    with open(path, "w", encoding="utf-8-sig") as f:   # Inno 要求带 BOM
        f.write(ISS_TEMPLATE.format(version=version, app_name=APP_NAME,
                                    setup_icon_line=line))
    return path


def build(version: str) -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[错误] 未安装 PyInstaller：pip install pyinstaller")
        return 2

    sep = ";" if os.name == "nt" else ":"
    icon = prepare_icon()
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--clean", "--windowed",
        "--name", APP_NAME,
        *(["--icon", icon] if icon else []),
        # 资源：模型 + 立绘整体随包
        "--add-data", f"brighteye/assets{sep}brighteye/assets",
        # mediapipe 的模型/动态库必须 collect-all，否则运行时缺文件
        "--collect-all", "mediapipe",
        "--collect-all", "cv2",
        # 多进程子进程入口（spawn）需显式收模块
        "--hidden-import", "brighteye.vision.worker",
        ENTRY,
    ]
    print("[打包]", " ".join(cmd))
    ret = subprocess.call(cmd, cwd=ROOT)
    if ret == 0:
        print(f"\n[完成] dist/{APP_NAME}/{APP_NAME}.exe")
        print(f"[提示] Inno Setup 脚本: {write_iss(version, icon)}")
    return ret


def main() -> None:
    parser = argparse.ArgumentParser(description="宸观 BrightEye 一键打包")
    parser.add_argument("--iss-only", action="store_true",
                        help="只生成 Inno Setup 脚本，不执行 PyInstaller")
    args = parser.parse_args()
    version = _version()
    if args.iss_only:
        print("[生成]", write_iss(version, prepare_icon()))
        return
    sys.exit(build(version))


if __name__ == "__main__":
    main()
