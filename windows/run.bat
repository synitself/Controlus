@echo off
REM Run Controlus from source (requires Python + dependencies installed).
cd /d "%~dp0"
python main.py %*
