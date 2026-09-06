@echo off
setlocal

rem Activate the build virtual environment and start the locally built
rem launcher copy (launcher\dist\...\WoT-Offline-Battles-Launcher.exe).
rem Rebuild with: server\build_windows_server.ps1, py -2.7 build_wotmod.py,
rem launcher\build_launcher.ps1 (venv first on PATH).

set "REPO_ROOT=%~dp0"
set "VENV_ROOT=%REPO_ROOT%.venv-build"
set "LAUNCHER_EXE=%REPO_ROOT%launcher\dist\WoT-Offline-Battles-Launcher\WoT-Offline-Battles-Launcher.exe"

if not exist "%VENV_ROOT%\Scripts\activate.bat" (
    echo Missing build venv: %VENV_ROOT%
    echo Create it with: py -3.12 -m venv .venv-build
    pause
    exit /b 2
)
if not exist "%LAUNCHER_EXE%" (
    echo Missing built launcher: %LAUNCHER_EXE%
    echo Build it first: powershell -NoProfile -File launcher\build_launcher.ps1
    pause
    exit /b 3
)

call "%VENV_ROOT%\Scripts\activate.bat"
start "" "%LAUNCHER_EXE%"

endlocal
