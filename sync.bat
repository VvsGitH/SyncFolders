@echo off
rem Wrapper for sync.py: keep it in the same folder as the script,
rem inside a directory included in the Windows PATH.
setlocal
set "SYNC_SCRIPT=%~dp0sync.py"
py -3 --version >nul 2>nul
if errorlevel 1 (
    python "%SYNC_SCRIPT%" %*
) else (
    py -3 "%SYNC_SCRIPT%" %*
)
exit /b %errorlevel%
