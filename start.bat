@echo off
chcp 65001 > nul
title WB Card Parser Bot - Local Host

echo ===================================================
echo   Запуск Telegram-бота карточек Wildberries
echo ===================================================
echo.

cd /d "%~dp0"

echo [1/2] Проверка и установка зависимостей...
python -m pip install -r requirements.txt --quiet

echo.
echo [2/2] Запуск бота (bot.py)...
echo ---------------------------------------------------
python bot.py

if %ERRORLEVEL% NEQ 0 (
    echo.
    echo ❌ Произошла ошибка при работе бота.
    pause
)
