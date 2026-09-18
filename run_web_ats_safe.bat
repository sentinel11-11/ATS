@echo off
cd /d "%~dp0"
set ATS_HOST=127.0.0.1
set ATS_PORT=8765
python server.py --host=127.0.0.1 --port=8765
pause
