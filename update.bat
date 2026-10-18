@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist .venv\Scripts\python.exe (call .venv\Scripts\activate.bat)
python update.py
pause
