@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0Select-PhotoSearchDataRoot.ps1" -Mode Configure
exit /b %ERRORLEVEL%
