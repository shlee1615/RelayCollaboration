@echo off
setlocal
if exist "%~dp0Relay.exe" goto executable
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\install.ps1" -CodexApp -Initialize -InstallSkills both
if errorlevel 1 goto failed
goto success
:executable
"%~dp0Relay.exe" install --agents both
if errorlevel 1 goto failed
:success
echo.
echo Installation complete. See docs\INSTALLATION.md for agent commands and Claude CLI setup.
pause
exit /b 0
:failed
echo.
echo Installation failed. Read the error above. The source-only package requires Python 3.11 or newer.
pause
exit /b 1
