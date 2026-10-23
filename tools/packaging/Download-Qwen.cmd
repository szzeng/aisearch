@echo off
setlocal
echo This downloads the pinned Qwen3-VL-Embedding-2B model (about 4.3 GB).
echo It needs about 9 GB of free space for staging and verified installation.
echo The 512D and 1024D providers share the same model files.
choice /C YN /N /M "Start the official fixed-revision download? [Y/N] "
if errorlevel 2 exit /b 0
"%~dp0PhotoSearch.exe" embedding download-qwen --confirm-large-download
set "RESULT=%ERRORLEVEL%"
if "%RESULT%"=="130" echo Download cancelled. No model was published.
if not "%RESULT%"=="0" if not "%RESULT%"=="130" echo Download failed. No model was published; see the error above.
echo.
pause
exit /b %RESULT%
