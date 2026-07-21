@echo off
chcp 65001 >nul
title BrightEye env setup
setlocal

rem ============================================================
rem   宸观 BrightEye · 运行环境一键安装（双击运行）
rem   步骤1 检查/自动安装 Python    步骤2 自动安装依赖库
rem   本文件位于 challenge\brighteye\ 下，工作目录切到 challenge\
rem ============================================================
cd /d "%~dp0.."

echo ==============================================
echo   宸观 BrightEye · 运行环境一键安装
echo   [1/2] 检查 Python   [2/2] 安装依赖库
echo ==============================================
echo.

set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY ( where py >nul 2>nul && set "PY=py" )
if defined PY goto :pip

echo [1/2] 未检测到 Python，开始自动安装……
where winget >nul 2>nul
if %errorlevel%==0 (
    echo       正在通过 winget 安装 Python 3.12（约 1~3 分钟，请稍候）……
    winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
) else (
    echo       未检测到 winget，改为下载官方安装器（约 25MB）……
    powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' -OutFile \"$env:TEMP\py_setup.exe\""
    echo       正在静默安装 Python（自动加入 PATH）……
    "%TEMP%\py_setup.exe" /passive InstallAllUsers=0 PrependPath=1 Include_test=0
)

rem 刚装完 PATH 不会进入当前窗口，按默认安装位置再找一遍
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do set "PY=%%D\python.exe"
if not defined PY ( where python >nul 2>nul && set "PY=python" )
if not defined PY (
    echo.
    echo [失败] Python 自动安装未完成。请到 https://www.python.org/downloads/
    echo        手动安装（勾选 Add python.exe to PATH），再重新双击本脚本。
    pause
    exit /b 1
)

:pip
echo [1/2] Python 就绪：
"%PY%" --version
echo.
echo [2/2] 安装依赖库 numpy / opencv / mediapipe（约 1~5 分钟）……
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
