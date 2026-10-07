@echo off
setlocal
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\quickstart.ps1"
set "relayExit=%ERRORLEVEL%"
echo.
pause
exit /b %relayExit%
