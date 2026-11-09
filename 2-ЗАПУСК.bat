@echo off
chcp 65001 >nul
cd /d "%~dp0"
where pythonw >nul 2>nul
if errorlevel 1 (
  set "PYW=C:\Users\donga\AppData\Local\Programs\Python\Python311\pythonw.exe"
) else (
  set "PYW=pythonw"
)
start "" "%PYW%" watcher.py
timeout /t 2 >nul
