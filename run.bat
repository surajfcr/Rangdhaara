@echo off
setlocal
cd /d "%~dp0"
title Rangdhaara

rem The Python environment lives outside OneDrive so sync can't lock pip's files.
set "VENV=%LOCALAPPDATA%\Rangdhaara\venv"

where python >nul 2>nul
if errorlevel 1 (
    echo Python 3.11 or newer is required. Install it from https://www.python.org/downloads/
    echo and tick "Add python.exe to PATH" during setup.
    pause
    exit /b 1
)

if not exist "%VENV%\Scripts\python.exe" (
    echo Setting up Python environment in %VENV% ...
    python -m venv "%VENV%"
    if errorlevel 1 ( pause & exit /b 1 )
)

echo Checking dependencies...
"%VENV%\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r requirements.txt
if errorlevel 1 (
    echo Could not install dependencies. Check your internet connection and try again.
    pause
    exit /b 1
)

"%VENV%\Scripts\python.exe" manage.py setup
if errorlevel 1 ( pause & exit /b 1 )

rem Open the browser a few seconds after the server starts.
start "" /b cmd /c "timeout /t 4 >nul & start "" http://localhost:8080"
"%VENV%\Scripts\python.exe" manage.py serve
pause
