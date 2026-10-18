@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Backend virtual environment is missing. Install the backend first.
  exit /b 1
)
if /i "%~1"=="--dry-run" (
  echo Job worker: "%~dp0run_worker.bat" --job-type all
  echo Cloudflare runner: "%~dp0scripts\run_cloudflare_worker.bat"
  exit /b 0
)
echo Starting job worker and Cloudflare runner in minimized windows.
echo Run this launcher once. Close each worker window to stop it when idle.
start "DomainCheck Job Worker" /min "%ComSpec%" /k call "%~dp0run_worker.bat" --job-type all
start "DomainCheck Cloudflare Runner" /min "%ComSpec%" /k call "%~dp0scripts\run_cloudflare_worker.bat"
exit /b 0
