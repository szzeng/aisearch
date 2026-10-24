@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Select-PhotoSearchDataRoot.ps1" -Mode Setup
set "RESULT=%ERRORLEVEL%"
echo.
if not "%RESULT%"=="0" echo Some model preparation steps failed or were skipped. Rerun this script to retry.
pause
exit /b %RESULT%
