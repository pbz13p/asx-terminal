@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist .env copy .env.example .env >nul
set PY=py
where py >nul 2>nul || set PY=python
%PY% update.py
%PY% server.py
pause
