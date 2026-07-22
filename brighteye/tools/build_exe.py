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
# PyInstaller 入口不能直接用 brighteye/main.py：它会被当顶层脚本运行，
# main.py 里的相对导入(from .config …)因无父包而 ImportError。
# 改为打包时生成绝对导入的启动器 build_entry.py（含 frozen 多进程 freeze_support）。
ENTRY = "build_entry.py"

ENTRY_CODE = '''"""PyInstaller 打包入口（由 build_exe.py 生成，勿手改）。"""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()   # frozen + spawn 子进程(--mp-vision)必需
    from brighteye.main import main
    main()
'''

# 排除重型库：--collect-all mediapipe 会把 mediapipe.tasks.python.genai
# （LLM 权重转换器，本项目不用）也收进来，连带拖入 torch(4GB+)/transformers
# 等，安装包从 ~600MB 膨胀到近 5GB。这些模块运行期从不导入，安全排除。
EXCLUDES = [
    "torch", "torchvision", "torchaudio", "transformers", "tokenizers",
    "safetensors", "pyarrow", "onnxruntime", "scipy", "pandas",
    "IPython", "jedi", "sympy",
]

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
UninstallFilesDir={{app}}
OutputDir=dist_installer
OutputBaseFilename={app_name}_Setup_v{version}
{setup_icon_line}
; 免管理员安装：普通用户直接装到 %LOCALAPPDATA%\Programs（数据目录天然可写），
; 需要装 Program Files 时安装向导会自行请求提权。
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Default.isl"

[Tasks]
; 默认勾选；可取消（离线铁律：不装大模型软件功能完整可用）
Name: "llm"; Description: "同时下载并安装本地大模型（Ollama + 聊天/复盘两模型，约 8.8GB，需联网；任一步下载失败自动回退删除并提示，不影响软件本体）"

[Files]
Source: "dist\{app_name}\*"; DestDir: "{{app}}"; Flags: recursesubdirs
Source: "brighteye\llm_models\*"; DestDir: "{{app}}\llm_models"

[Icons]
Name: "{{group}}\宸观 BrightEye"; Filename: "{{app}}\{app_name}.exe"
Name: "{{group}}\卸载 宸观 BrightEye"; Filename: "{{uninstallexe}}"
Name: "{{autodesktop}}\宸观 BrightEye"; Filename: "{{app}}\{app_name}.exe"

[Run]
Filename: "{{app}}\{app_name}.exe"; Description: "立即启动 宸观 BrightEye"; \
    Flags: nowait postinstall skipifsilent

[Code]
// —— 安装收尾：勾选大模型任务时，运行回退安全的模型安装脚本 ——
// 脚本内任一下载失败会自行 ollama rm 清掉半成品并返回非 0；这里只负责弹窗提醒。
// 软件本体自带运行时（无需下载 Python/依赖库），故失败仅影响 AI 增强，不回滚本体。
procedure CurStepChanged(CurStep: TSetupStep);
var
  Bat: string;
  ResultCode: Integer;
begin
  if CurStep = ssPostInstall then
  begin
    if WizardIsTaskSelected('llm') then
    begin
      Bat := ExpandConstant('{{app}}\llm_models\install_models_setup.bat');
      if Exec(ExpandConstant('{{cmd}}'), '/C "' + Bat + '"', '',
              SW_SHOW, ewWaitUntilTerminated, ResultCode) then
      begin
        if ResultCode <> 0 then
          MsgBox('大模型下载/安装失败，已自动回退删除未完成的模型文件。' #13#10 #13#10
                 + '软件本体不受影响，全部监测功能可离线使用；' #13#10
                 + '联网后可随时重跑 安装目录\llm_models\install_models_setup.bat 补装。',
                 mbError, MB_OK);
      end
      else
        MsgBox('无法启动大模型安装脚本，可稍后手动运行' #13#10
               + '安装目录\llm_models\install_models_setup.bat 补装。',
               mbError, MB_OK);
    end;
  end;
end;

// —— 卸载收尾：删用户数据与已下载大模型（默认删）；——
// 保留 Python 环境与 Ollama 程序本体（属共享系统组件，其他软件可能在用）。
procedure RemoveModels();
var
  ResultCode: Integer;
  Exe: string;
begin
  Exe := ExpandConstant('{{localappdata}}\Programs\Ollama\ollama.exe');
  if not FileExists(Exe) then
    Exe := 'ollama';
  Exec(ExpandConstant('{{cmd}}'),
       '/C ""' + Exe + '" rm qwen2.5:7b-instruct & "' + Exe + '" rm deepseek-r1:7b"',
       '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: string;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{{localappdata}}\ChenguanBrightEye');
    if DirExists(DataDir) then
      if MsgBox('是否同时删除用户数据（监测历史 / 健康报告）？' #13#10
                + DataDir + #13#10 #13#10
                + '选[否]则保留，重装后历史档案不丢。',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDYES then
        DelTree(DataDir, True, True, True);
    if MsgBox('是否删除已下载的本地大模型（约 8.8GB）？' #13#10
              + 'qwen2.5:7b-instruct 与 deepseek-r1:7b' #13#10 #13#10
              + '（仅删模型；Ollama 程序与 Python 环境保留，不影响其他软件）',
              mbConfirmation, MB_YESNO or MB_DEFBUTTON1) = IDYES then
      RemoveModels();
  end;
end;
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


def find_iscc() -> str:
    """定位 Inno Setup 6 编译器 ISCC.exe（PATH / 常见安装路径），找不到返回空串。"""
    exe = shutil.which("ISCC")
    if exe:
        return exe
    for base in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
        cand = os.path.join(base, "Inno Setup 6", "ISCC.exe")
        if os.path.isfile(cand):
            return cand
    return ""


def compile_installer(iss_path: str) -> int:
    """用 ISCC 编译安装包；未装 Inno Setup 时提示手动编译（不算失败）。"""
    iscc = find_iscc()
    if not iscc:
        print("[提示] 未检测到 Inno Setup 6，跳过安装包编译。"
              "安装后右键 build_installer.iss → Compile 即可。")
        return 0
    print(f"[安装包] {iscc} {iss_path}")
    ret = subprocess.call([iscc, iss_path], cwd=ROOT)
    if ret == 0:
        print(f"[完成] 安装包已输出到 {os.path.join(ROOT, 'dist_installer')}")
    else:
        print(f"[错误] ISCC 编译失败（exit={ret}）")
    return ret


def build(version: str) -> int:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("[错误] 未安装 PyInstaller：pip install pyinstaller")
        return 2

    entry_path = os.path.join(ROOT, ENTRY)
    with open(entry_path, "w", encoding="utf-8") as f:
        f.write(ENTRY_CODE)

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
        # 排除误收的重型库（见文件头 EXCLUDES 说明）
        *(arg for mod in EXCLUDES for arg in ("--exclude-module", mod)),
        ENTRY,
    ]
    print("[打包]", " ".join(cmd))
    ret = subprocess.call(cmd, cwd=ROOT)
    if ret == 0:
        print(f"\n[完成] dist/{APP_NAME}/{APP_NAME}.exe")
        iss = write_iss(version, icon)
        print(f"[提示] Inno Setup 脚本: {iss}")
        ret = compile_installer(iss)   # 装了 Inno Setup 就顺手编出 setup.exe
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
