@echo off
setlocal
cd /d "%~dp0"
if not exist "backend\.venv\Scripts\python.exe" (
  echo Backend virtualenv not found. Create backend.venv and install backend dependencies first.
  exit /b 1
)
"backend\.venv\Scripts\python.exe" -m domaincheck.workers.cli
