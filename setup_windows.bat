@echo off
setlocal
cd /d "%~dp0"
set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not exist "%PYTHON%" (
  where winget >nul 2>&1 || (echo winget is required for automatic installation. & exit /b 1)
  winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements || exit /b 1
)
where ffmpeg >nul 2>&1 || (
  winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements || exit /b 1
)
where ffmpeg >nul 2>&1 || call :copy_ffmpeg
if errorlevel 1 exit /b 1
if exist bin\ffmpeg.exe set "PATH=%CD%\bin;%PATH%"
if not exist "%PYTHON%" (echo Python 3.11 was not found at %PYTHON% & exit /b 1)
"%PYTHON%" -m venv venv || exit /b 1
venv\Scripts\python.exe -m pip install --upgrade pip setuptools wheel || exit /b 1
venv\Scripts\python.exe -m pip install -r requirements.txt || exit /b 1
venv\Scripts\python.exe main.py --bootstrap || exit /b 1
venv\Scripts\python.exe main.py --download-demo || exit /b 1
echo Ready. Double-click run_ui.bat or run_demo.bat.
pause
exit /b 0

:copy_ffmpeg
if not exist bin mkdir bin
powershell -NoProfile -Command "$root=Join-Path $env:LOCALAPPDATA 'Microsoft\WinGet\Packages'; $exe=Get-ChildItem $root -Filter ffmpeg.exe -Recurse -ErrorAction SilentlyContinue | Where-Object { $_.FullName -like '*Gyan.FFmpeg*' } | Select-Object -First 1; if (!$exe) { exit 1 }; Copy-Item $exe.FullName 'bin\ffmpeg.exe'; Copy-Item (Join-Path $exe.DirectoryName 'ffprobe.exe') 'bin\ffprobe.exe'"
exit /b %errorlevel%
