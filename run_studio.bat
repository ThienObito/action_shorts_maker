@echo off
setlocal
cd /d "%~dp0"
start "IronAxis Studio" /min "%~dp0venv\Scripts\python.exe" "%~dp0studio_server.py"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8765"
endlocal
