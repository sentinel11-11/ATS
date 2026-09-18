@echo off
cd /d "%~dp0\xp_bridge"
python -m pip install pyinstaller
pyinstaller --onefile --name ATS_XP_Bridge xp_bridge.py
pause
