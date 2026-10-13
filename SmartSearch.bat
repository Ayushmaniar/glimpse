@echo off
rem Start SmartSearch in the background, then press Alt+Space. Run install.bat once first.
cd /d "%~dp0"
if not exist .venv\Scripts\pythonw.exe (
  echo SmartSearch is not installed yet. Run install.bat first.
  pause
  exit /b 1
)
start "" .venv\Scripts\pythonw.exe -m smartsearch.app
