@echo off
setlocal
cd /d "%~dp0"
title Disclosure Intelligence
where python >nul 2>nul
if errorlevel 1 (
  echo Python is required to run the live dashboard.
  echo Install Python from python.org and check "Add Python to PATH" during setup.
  echo.
  pause
  exit /b 1
)
if not exist ".venv\Scripts\python.exe" (
  echo First-time setup: preparing Disclosure Intelligence...
  python -m venv .venv
  call .venv\Scripts\activate.bat
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
python web_app.py
endlocal
