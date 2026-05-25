@echo off
:: Daily Skroutz pipeline launcher for Windows Task Scheduler.
:: Redirects all output to a dated log file so failures are debuggable.
::
:: SETUP: Update PYTHON below to point to your Python interpreter.

set "PROJECT=%~dp0"
set "PROJECT=%PROJECT:~0,-1%"
set PYTHON=C:\Users\StavrosKV\anaconda33\python.exe
set LOG_DIR=%PROJECT%\logs

if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

:: Build a timestamp for the log filename (YYYY-MM-DD)
for /f "tokens=1-3 delims=/-" %%a in ("%DATE%") do (
    set DD=%%a
    set MM=%%b
    set YYYY=%%c
)
set LOGFILE=%LOG_DIR%\pipeline_%YYYY%-%MM%-%DD%.log

echo Pipeline started at %DATE% %TIME% >> "%LOGFILE%"
"%PYTHON%" "%PROJECT%\run_pipeline.py" >> "%LOGFILE%" 2>&1
echo Pipeline exited with code %ERRORLEVEL% at %DATE% %TIME% >> "%LOGFILE%"
