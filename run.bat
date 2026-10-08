@echo off
title Item Checker - Qwen Vision Server
cd /d "%~dp0"

echo ===================================================================
echo   Akeneo Item Checker ^| Qwen Vision AI ^& Obsidian Knowledge Base
echo ===================================================================
echo.
echo [1/2] Launching browser at http://localhost:8089 ...
timeout /t 1 /nobreak >nul
start http://localhost:8089

echo [2/2] Starting Python server...
echo.
python qwen_server.py
pause
