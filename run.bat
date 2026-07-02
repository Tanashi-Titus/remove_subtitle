@echo off
REM TNT GROUP - chay nhanh tren Windows
cd /d "%~dp0"
where python >nul 2>nul || (echo Chua cai Python. & pause & exit /b 1)
if not exist .venv ( python -m venv .venv )
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
python -m playwright install chromium
python app.py
pause
