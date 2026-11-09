@echo off
chcp 65001 >nul
title Before game
taskkill /IM OneDrive.exe /F >nul 2>nul
taskkill /IM uTorrent.exe /F >nul 2>nul
taskkill /IM msedge.exe /F >nul 2>nul
taskkill /IM claude.exe /F >nul 2>nul
taskkill /IM FreeLLMAPI.exe /F >nul 2>nul
timeout /t 2 >nul
start steam://rungameid/730
exit
