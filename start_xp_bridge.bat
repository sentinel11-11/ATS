@echo off
cd /d "%~dp0xp_bridge"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 xp_bridge.py
) else (
  python xp_bridge.py
)
pause
