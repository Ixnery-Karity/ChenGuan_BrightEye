@echo off
chcp 65001 >nul
title BrightEye env setup
setlocal EnableDelayedExpansion

rem ============================================================
rem   宸观 BrightEye · 运行环境一键安装（双击运行）
rem   [1/2] 定位/安装合适版本的 Python（3.10~3.14，防新旧版本冲突）
rem   [2/2] 用该解释器安装依赖库，并把解释器绝对路径固定写入
rem         brighteye\python_path.txt —— 启动器只认它，多版本共存不串
rem ============================================================
cd /d "%~dp0.."

echo ==============================================
echo   宸观 BrightEye · 运行环境一键安装
echo   [1/2] 检查 Python   [2/2] 安装依赖库
echo ==============================================
echo.

set "PY="

rem ① 上次固定过的解释器仍在 → 直接复用（零冲突）
if exist "brighteye\python_path.txt" (
    set /p PY=<"brighteye\python_path.txt"
    if defined PY if not exist "!PY!" set "PY="
)
if defined PY goto :pip

rem ② PATH 里的 python：必须通过版本检查（3.10~3.14）才接受
where python >nul 2>nul
if %errorlevel%==0 (
    python -c "import sys; sys.exit(0 if (3,10)<=sys.version_info[:2]<=(3,14) else 1)" >nul 2>nul
    if !errorlevel!==0 (
        for /f "delims=" %%P in ('python -c "import sys;print(sys.executable)"') do set "PY=%%P"
    ) else (
        echo       检测到 PATH 中的 Python 版本不在 3.10~3.14 支持范围，跳过之，
        echo       将使用/安装独立的 Python 3.12（不动你现有的 Python）。
    )
)
if defined PY goto :pip

rem ③ py 启动器里挑一个支持版本（优先 3.12）
for %%V in (3.12 3.11 3.13 3.10) do (
    if not defined PY (
        py -%%V -c "print(1)" >nul 2>nul
        if !errorlevel!==0 (
            for /f "delims=" %%P in ('py -%%V -c "import sys;print(sys.executable)"') do set "PY=%%P"
        )
    )
)
if defined PY goto :pip

rem ④ 都没有 → 安装独立 Python 3.12（仅当前用户，不改系统已有版本）
echo [1/2] 未找到可用的 Python，开始自动安装 Python 3.12……
where winget >nul 2>nul
if %errorlevel%==0 (
    echo       正在通过 winget 安装（约 1~3 分钟，请稍候）……
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
) else (
    echo       未检测到 winget，改为下载官方安装器（约 25MB）……
    powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile \"$env:TEMP\py_setup.exe\""
    echo       正在静默安装 Python（仅当前用户）……
    "%TEMP%\py_setup.exe" /passive InstallAllUsers=0 PrependPath=1 Include_test=0
)

rem 刚装完 PATH 不进当前窗口 → 按默认安装位置定位（优先 3.12）
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python312*") do set "PY=%%D\python.exe"
if not defined PY for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do set "PY=%%D\python.exe"
if not defined PY (
    echo.
    echo [失败] Python 自动安装未完成。请到 https://www.python.org/downloads/
    echo        手动安装 Python 3.12（勾选 Add python.exe to PATH），再重新双击本脚本。
    pause
    exit /b 1
)

:pip
echo [1/2] Python 就绪：
"%PY%" --version
rem 固定解释器：启动器与后续安装只认这一个，杜绝多版本冲突
>"brighteye\python_path.txt" echo %PY%
echo       已固定解释器路径到 brighteye\python_path.txt
echo.
echo [2/2] 安装依赖库 numpy / opencv / mediapipe / pillow（约 1~5 分钟）……
"%PY%" -m pip install --upgrade pip >nul 2>nul
"%PY%" -m pip install -r brighteye\requirements.txt
if not %errorlevel%==0 (
    echo       直连较慢或失败，切换清华镜像重试……
    "%PY%" -m pip install -r brighteye\requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
)
if not %errorlevel%==0 (
    echo.
    echo [提示] 依赖安装未全部成功。宸观 BrightEye 支持离线降级：
    echo        仅装上 numpy 也能以模拟数据完整演示（摄像头功能需 opencv+mediapipe）。
    pause
    exit /b 1
)

echo.
echo [完成] 运行环境就绪！双击「brighteye\启动宸观BrightEye.vbs」即可使用。
pause
