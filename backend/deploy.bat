@echo off
setlocal
cd /d "%~dp0"
python "%~dp0scripts\deploy_team.py" %*
