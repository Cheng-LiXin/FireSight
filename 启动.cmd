@echo off
rem Double-click entry: start the FireSight service (REST + built console UI).
rem ASCII only on purpose -- cmd.exe reads .cmd files in the ANSI codepage, so
rem non-ASCII text here gets garbled. All Chinese output comes from the .ps1.
rem
rem -AutoBuild: rebuild the console UI first when its sources are newer than
rem dist/. Without it a double-click silently serves yesterday's pages and the
rem symptom looks like "my edit did nothing", which is the slowest kind of bug.
chcp 65001 >nul
setlocal
set "PS=powershell"
where pwsh >nul 2>nul && set "PS=pwsh"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\firesight.ps1" start -AutoBuild %*
set "RC=%ERRORLEVEL%"
echo.
if not "%RC%"=="0" echo Exit code: %RC%
echo (press any key to close this window)
pause >nul
endlocal
exit /b %RC%
