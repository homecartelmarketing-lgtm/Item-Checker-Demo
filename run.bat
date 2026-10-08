@echo off
title Item Checker - Qwen Vision Server
cd /d "%~dp0"

:: Read DASHSCOPE_API_KEY from .env
if exist .env (
    for /f "usebackq tokens=1,* delims==" %%A in (".env") do (
        if "%%A"=="DASHSCOPE_API_KEY" set "DASHSCOPE_API_KEY=%%B"
    )
)

echo ===================================================================
echo   Akeneo Item Checker ^| Qwen Vision AI ^& Visual Matcher
echo ===================================================================
echo.
echo [1/2] Launching browser at http://localhost:8089 ...
timeout /t 1 /nobreak >nul
start http://localhost:8089

echo [2/2] Starting Python server...
echo.
python qwen_server.py
pause
