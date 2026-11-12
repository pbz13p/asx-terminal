@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=py
where py >nul 2>nul || set PY=python
%PY% update.py
%PY% study.py
start "" http://127.0.0.1:8765/study.html
pause
