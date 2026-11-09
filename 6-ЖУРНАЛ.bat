@echo off
chcp 65001 >nul
cd /d "%~dp0"
powershell -NoProfile -Command "if (Test-Path 'watcher.log') { Get-Content 'watcher.log' -Tail 25 -Encoding UTF8 } else { Write-Host 'watcher.log not found' }"
echo.
pause
