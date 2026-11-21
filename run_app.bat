@echo off
title YT Screen Extractor
cd /d "%~dp0"

echo ========================================================
echo               YT SCREEN EXTRACTOR
echo ========================================================
echo.

:: 1. Check if Python is installed
where python >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Python is not found on your system!
    echo Please install Python 3.10 or higher from https://www.python.org/
    echo (Make sure to check "Add Python to PATH" during installation)
    echo.
    pause
    exit /b 1
)

:: 2. Check if virtual environment exists; if not, create and install
if not exist ".venv\Scripts\python.exe" (
    echo [SETUP] First-time setup detected. Setting up local environment...
    python -m venv .venv
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
    echo [SETUP] Installing required packages (yt-dlp, opencv, streamlit, reportlab)...
    call .venv\Scripts\pip install -r requirements.txt
    if %ERRORLEVEL% NEQ 0 (
        echo [ERROR] Failed to install dependencies.
        pause
        exit /b 1
    )
    echo [SETUP] Setup completed successfully!
    echo.
)

:: 3. Launch application and open browser
echo Starting YT Screen Extractor...
start http://localhost:8501
call .venv\Scripts\streamlit.exe run app.py --server.port 8501

pause
