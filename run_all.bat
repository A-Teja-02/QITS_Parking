@echo off
setlocal enabledelayedexpansion
echo ========================================================
echo   Starting Quadrant Parking Surveillance Suite
echo ========================================================
echo.

:: Detect current active network IPv4 address
set CURRENT_IP=172.16.2.164
for /f "tokens=4" %%A in ('route print 0.0.0.0 ^| findstr 0.0.0.0') do (
    set CURRENT_IP=%%A
)

echo Detected Network IP: %CURRENT_IP%
echo.

set VENV_PYTHON="c:\Users\RamaKrishnaSamavedam\OneDrive - QUADRANT IT PRIVATE LIMITED\Desktop\cctv object detc\venv\Scripts\python.exe"
if not exist %VENV_PYTHON% (
    if exist "%~dp0venv\Scripts\python.exe" (
        set VENV_PYTHON="%~dp0venv\Scripts\python.exe"
    ) else if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
        set VENV_PYTHON="%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
    ) else (
        set VENV_PYTHON=python
    )
)

echo [1/3] Starting QITS Parking Server (Port 8002 on 0.0.0.0)...
start "QITS Parking Server :8002" cmd /k "cd /d %~dp0server && %VENV_PYTHON% -m uvicorn main:app --host 0.0.0.0 --port 8002 --reload"

echo [2/3] Starting AI Vision & ANPR Engine (Port 8001 on 0.0.0.0)...
start "AI Vision & ANPR :8001" cmd /k "cd /d %~dp0backend && %VENV_PYTHON% -m uvicorn main:app --host 0.0.0.0 --port 8001 --reload"

echo [3/3] Starting React Vite Client (Port 5173 on 0.0.0.0)...
start "QITS Parking Client :5173" cmd /k "cd /d %~dp0client && npm run dev -- --host 0.0.0.0"

echo.
echo ========================================================
echo   All services launched successfully!
echo ========================================================
echo   Localhost Access (This PC):
echo     - Web App:       http://localhost:5173
echo     - Parking API:   http://localhost:8002/docs
echo     - AI & ANPR API: http://localhost:8001/docs
echo.
echo   Network Access (Any device on current network):
echo     - Web App:       http://%CURRENT_IP%:5173
echo     - Parking API:   http://%CURRENT_IP%:8002/docs
echo     - AI & ANPR API: http://%CURRENT_IP%:8001/docs
echo ========================================================
pause
