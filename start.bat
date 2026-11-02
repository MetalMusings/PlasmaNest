@echo off
cd /d "%~dp0"
python plasmanest.py %*
if errorlevel 1 pause
