@echo off
title Watchlist
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 main.py %*
) else (
    python main.py %*
)
if errorlevel 1 (
    echo.
    echo Watchlist stopped with an error. If Python isn't installed, get it from https://www.python.org/downloads/
    pause
)
