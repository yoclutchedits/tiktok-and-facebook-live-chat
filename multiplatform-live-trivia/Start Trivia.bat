@echo off
setlocal
cd /d "%~dp0"
title Multiplatform LIVE Trivia

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Creating the Python virtual environment...
    where py >nul 2>nul
    if not errorlevel 1 (
        py -3.12 -m venv .venv
    ) else (
        python -m venv .venv
    )
    if errorlevel 1 goto :error
)

set "PYTHON_EXE=%CD%\.venv\Scripts\python.exe"
echo [2/3] Checking/installing required packages...
"%PYTHON_EXE%" -m pip install --upgrade pip
if errorlevel 1 goto :error
"%PYTHON_EXE%" -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo [3/3] Starting Multiplatform LIVE Trivia...
start "" powershell -NoProfile -WindowStyle Hidden -Command "$until=(Get-Date).AddSeconds(60); do { Start-Sleep -Seconds 1; try { $r=Invoke-WebRequest 'http://127.0.0.1:8000' -TimeoutSec 2; if($r.StatusCode -eq 200){ Start-Process 'http://127.0.0.1:8000'; exit } } catch {} } while((Get-Date) -lt $until)"
"%PYTHON_EXE%" main.py
set "EXIT_CODE=%ERRORLEVEL%"
echo.
echo Trivia server stopped with exit code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%

:error
echo.
echo Could not prepare or start the application. Check that Python 3.11 or newer is installed and available on PATH.
pause
exit /b 1
