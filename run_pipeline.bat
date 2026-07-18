@echo off
:: Daily Skroutz pipeline launcher for Windows Task Scheduler.
:: Python handles its own logging to logs/pipeline_YYYY-MM-DD.log via FileHandler.
::
:: Launches via run_pipeline_wrapper.ps1 (not python.exe directly) so that a
:: missing interpreter or hard crash before Python's own alerting is up
:: (broken imports, etc.) still fires a Telegram alert -- see the wrapper's
:: header comment for the exact rule used to avoid duplicate alerts.
::
:: SETUP: Update PYTHON inside run_pipeline_wrapper.ps1 if your interpreter
:: path changes.

set "PROJECT=%~dp0"
set "PROJECT=%PROJECT:~0,-1%"

cd /d "%PROJECT%"

powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%PROJECT%\run_pipeline_wrapper.ps1"
exit /b %ERRORLEVEL%
