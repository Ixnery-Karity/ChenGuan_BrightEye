@echo off
chcp 65001 >nul
title BrightEye LLM setup
rem 宸观 BrightEye · 大模型安装（安装向导调用版，非交互）
rem 退出码：0=成功  1=Ollama 安装失败  2=模型下载失败（已回退删除半成品）
setlocal

set "OLLAMA=ollama"
where ollama >nul 2>nul
if %errorlevel%==0 goto :serve
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
    goto :serve
)
echo [1/3] 安装 Ollama（约 300MB）……
winget install -e --id Ollama.Ollama --accept-source-agreements --accept-package-agreements
if not %errorlevel%==0 exit /b 1
if exist "%LOCALAPPDATA%\Programs\Ollama\ollama.exe" (
    set "OLLAMA=%LOCALAPPDATA%\Programs\Ollama\ollama.exe"
) else (
    where ollama >nul 2>nul || exit /b 1
)

:serve
rem 确保服务在跑（pull 需要本地服务；已在跑则此启动无害）
start "" /min "%OLLAMA%" serve >nul 2>nul
timeout /t 5 /nobreak >nul

echo [2/3] 下载聊天模型 qwen2.5:7b-instruct（约 4.7GB，视网速数分钟到数十分钟）……
"%OLLAMA%" pull qwen2.5:7b-instruct
if not %errorlevel%==0 goto :rollback

echo [3/3] 下载复盘模型 deepseek-r1:7b（约 4.7GB）……
"%OLLAMA%" pull deepseek-r1:7b
if not %errorlevel%==0 goto :rollback

echo [完成] 两个大模型就绪，宸观 BrightEye 启动时会自动接入。
exit /b 0

:rollback
echo [回退] 下载失败，正在清理已下载的模型半成品……
"%OLLAMA%" rm qwen2.5:7b-instruct >nul 2>nul
"%OLLAMA%" rm deepseek-r1:7b >nul 2>nul
exit /b 2
