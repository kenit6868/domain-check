@echo off
call "%~dp0backend\run_worker.bat" --job-type domain_worker_precheck %*
