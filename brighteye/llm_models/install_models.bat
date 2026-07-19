@echo off
rem 宸观 BrightEye · 大模型一键安装（Windows）
rem 流程：检测 Ollama → 缺失则 winget 安装 → 拉取聊天/复盘两个模型（共约 8.8GB）
chcp 65001 >nul
echo ==============================================
echo   宸观 BrightEye 大模型一键安装
echo ==============================================

where ollama >nul 2>nul
if %errorlevel%==0 goto pull
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Ollama"
    goto pull
)

echo [1/3] 未检测到 Ollama，尝试通过 winget 安装...
winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
if %errorlevel% neq 0 (
    echo.
    echo [!] winget 安装失败，请手动到 https://ollama.com/download 下载安装后重跑本脚本。
    pause
    exit /b 1
)
set "PATH=%PATH%;%LOCALAPPDATA%\Programs\Ollama"

:pull
echo [2/3] 拉取聊天模型 qwen2.5:7b-instruct（约 4.7GB，视网速需数分钟）...
ollama pull qwen2.5:7b-instruct
if %errorlevel% neq 0 echo [!] 聊天模型拉取失败，可稍后重跑本脚本续传。

echo [3/3] 拉取复盘模型 deepseek-r1:7b（约 4.7GB）...
ollama pull deepseek-r1:7b
if %errorlevel% neq 0 echo [!] 复盘模型拉取失败，可稍后重跑本脚本续传。

echo.
echo 完成！直接启动宸观 BrightEye 即可，软件会自动拉起 Ollama 服务。
echo （低配机可改用小模型，见本目录 README.md）
pause
