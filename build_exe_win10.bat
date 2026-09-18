@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m pip install -r requirements.txt
  py -3 -m PyInstaller --noconfirm --clean --onefile --name ATS_Dispatcher --add-data "static;static" server.py
  py -3 -m PyInstaller --noconfirm --clean --onefile --name ATS_6Line_Bridge --hidden-import serial xp_bridge\xp_bridge.py
) else (
  python -m pip install -r requirements.txt
  python -m PyInstaller --noconfirm --clean --onefile --name ATS_Dispatcher --add-data "static;static" server.py
  python -m PyInstaller --noconfirm --clean --onefile --name ATS_6Line_Bridge --hidden-import serial xp_bridge\xp_bridge.py
)
pause
