@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  set "PY=C:\Users\donga\AppData\Local\Programs\Python\Python311\python.exe"
) else (
  set "PY=python"
)
"%PY%" setup.py
if errorlevel 9009 python setup.py
echo.
pause
