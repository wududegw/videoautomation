@echo off
setlocal

rem ============================================================
rem  MoneyPrinterTurbo (Long) - stop & restart the WebUI
rem  Double-click this file, or run: .\restart.bat
rem ============================================================

set "PORT=508"

rem Always run from the folder this script lives in.
cd /d "%~dp0"

rem Pick the venv Python if it exists, otherwise fall back to system Python.
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=python"
)

echo.
echo === Stopping any server on port %PORT% ===
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    echo Killing PID %%P
    taskkill /PID %%P /F >nul 2>&1
)

echo.
echo === Starting WebUI on port %PORT% ===
echo Using Python: %PY%
echo Open http://localhost:%PORT% once it finishes loading.
echo Press Ctrl+C in this window to stop the server.
echo.

"%PY%" -m streamlit run ".\webui\Main.py" --browser.gatherUsageStats=False --server.enableCORS=True --server.port=%PORT%

endlocal
