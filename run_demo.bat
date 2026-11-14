@echo off
cd /d "%~dp0"
if not exist "venv\Scripts\python.exe" (echo Run setup_windows.bat first. & pause & exit /b 1)
venv\Scripts\python.exe main.py --test-demo
if errorlevel 1 (echo Demo failed. See the error above. & pause & exit /b 1)
echo Completed. Open the outputs folder to view your Shorts.
pause
