@echo off
:: Daily Skroutz pipeline launcher for Windows Task Scheduler.
:: Python handles its own logging to logs/pipeline_YYYY-MM-DD.log via FileHandler.
::
:: SETUP: Update PYTHON below to point to your Python interpreter.

set "PROJECT=%~dp0"
set "PROJECT=%PROJECT:~0,-1%"
set PYTHON=C:\Users\StavrosKV\anaconda33\python.exe

cd /d "%PROJECT%"

"%PYTHON%" "%PROJECT%\run_pipeline.py"
