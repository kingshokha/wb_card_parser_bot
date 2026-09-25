@echo off
title WB Card Parser Bot - Local Host

echo ===================================================
echo   WB Card Parser - Telegram bot
echo ===================================================
echo.

cd /d "%~dp0"

echo [1/2] Installing dependencies...
python -m pip install -r requirements.txt --quiet
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] Failed to install dependencies. Is Python installed and on PATH?
    pause
    exit /b 1
)

echo.
echo [2/2] Starting bot (bot.py)...
echo ---------------------------------------------------
python bot.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo [ERROR] The bot stopped with an error.
)
pause
