@echo off
REM CHL API Server Startup Script
REM Detects the appropriate venv and starts the API server

REM Detect which venv exists and activate it
if exist ".venv-cpu\" (
    echo Activating CPU mode venv...
    call .venv-cpu\Scripts\activate.bat
) else if exist ".venv-nvidia\" (
    echo Activating NVIDIA GPU mode venv...
    call .venv-nvidia\Scripts\activate.bat
) else if exist ".venv-amd\" (
    echo Activating AMD GPU mode venv...
    call .venv-amd\Scripts\activate.bat
) else if exist ".venv-intel\" (
    echo Activating Intel GPU mode venv...
    call .venv-intel\Scripts\activate.bat
) else (
    echo ERROR: No API server venv found!
    echo Please install the API server first ^(see README.md Step 1^)
    exit /b 1
)

echo Starting CHL API server on http://127.0.0.1:8000
echo Press Ctrl+C to stop
echo.

REM Start the API server
python -m uvicorn src.api.server:app --host 127.0.0.1 --port 8000
