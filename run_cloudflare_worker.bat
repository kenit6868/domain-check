@echo off
setlocal
cd /d "%~dp0backend"
if not exist ".venv\Scripts\python.exe" (
  echo Backend virtual environment is missing. Install backend first.
  exit /b 1
)
".venv\Scripts\python.exe" -m domaincheck.cloudflare_runner
