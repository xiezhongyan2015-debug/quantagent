@echo off
setlocal
cd /d "%~dp0..\.."
if not exist ".venv\Scripts\python.exe" (
  echo Please complete the setup in tools\choice_trader\README.md first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" "tools\choice_trader\console_app.py"
if errorlevel 1 pause
