@echo off
rem Double-click entry: stop the FireSight service (kills the whole process tree,
rem because .venv\Scripts\python.exe is a redirector and leaves a child behind).
rem ASCII only on purpose -- see the note in the start wrapper.
chcp 65001 >nul
setlocal
set "PS=powershell"
where pwsh >nul 2>nul && set "PS=pwsh"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\firesight.ps1" stop %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo Exit code: %RC%
echo (press any key to close this window)
pause >nul
endlocal
exit /b %RC%
