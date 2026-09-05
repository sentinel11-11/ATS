@echo off
cd /d "%~dp0"
if not exist dist\ATS_Web_Dispatcher.exe (
 echo First run build_exe_win10.bat
 pause
 exit /b 1
)
iscc build_setup.iss
pause
