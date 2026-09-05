@echo off
rem ATS v2 — запуск (Windows)
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -m app.run %*
) else (
  python -m app.run %*
)
pause
