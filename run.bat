@echo off
rem Starts Pasig DASRS at http://127.0.0.1:5000 (MySQL must be running in XAMPP).
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Setting up Python environment, first run only...
    python -m venv .venv || goto :error
    ".venv\Scripts\python" -m pip install -r requirements.txt || goto :error
    ".venv\Scripts\python" setup_db.py || goto :error
)

rem open the browser once the server has had a moment to start
start "" cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:5000"
set HOST=127.0.0.1
".venv\Scripts\python" app.py
goto :eof

:error
echo.
echo Setup failed. Make sure Python is installed and MySQL is running in XAMPP.
pause
