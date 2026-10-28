@echo off
rem ============================================================================
rem  ReviewHunter one-click installer for Windows
rem
rem  Double-click this file. It works in two situations:
rem    * inside the unzipped ReviewHunter folder (uses the files next to it)
rem    * on its own, downloaded from the Releases page (downloads the latest app)
rem
rem  Steps: 1) find Python 3.10+; if missing, install a private Python for this user
rem            with uv (no administrator rights, no prompts; winget is the fallback)
rem         2) locate or download the app      3) open the graphical installer
rem  Options are forwarded, e.g.  Install-ReviewHunter.bat --cli --yes
rem ============================================================================
setlocal EnableExtensions
title ReviewHunter Setup
cd /d "%~dp0"
set "ZIP_URL=https://github.com/Andrianarivelo/ReviewHunter/releases/latest/download/ReviewHunter.zip"
set "PROBE=import sys; sys.version_info[:2] >= (3, 10) and print(sys.executable)"

echo.
echo   ReviewHunter Setup
echo   ------------------
echo.

rem ---------------------------------------------------------------- 1) Python
call :find_python
if defined PY goto :have_python
echo   Python 3.10 or newer was not found: installing a private Python for your account
echo   (no administrator rights needed, about 30 MB, one time)...
call :private_python
if defined PY goto :have_python
echo   The private Python could not be installed.
where winget >nul 2>nul
if errorlevel 1 goto :no_python
echo   It can be installed now with the Windows package manager (winget).
echo   winget may ask you to accept the source and Python license terms.
echo.
choice /C YN /M "  Install Python 3.12 for your user account now"
if errorlevel 2 goto :no_python
winget install -e --id Python.Python.3.12 --scope user
call :find_python
if defined PY goto :have_python

:no_python
echo.
echo   Please install Python from https://www.python.org/downloads/
echo   (tick "Add python.exe to PATH"), then double-click this file again.
start "" "https://www.python.org/downloads/"
pause
exit /b 1

:have_python
echo   Using Python: %PY%

rem ---------------------------------------------------------------- 2) app files
set "INSTALLER=%~dp0installer\reviewhunter_installer.py"
if exist "%INSTALLER%" goto :run_installer
set "DL=%TEMP%\ReviewHunterSetup"
echo   Downloading the latest ReviewHunter release...
if exist "%DL%" rmdir /s /q "%DL%"
mkdir "%DL%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; try { Invoke-WebRequest -UseBasicParsing $env:ZIP_URL -OutFile (Join-Path $env:DL 'ReviewHunter.zip') -ErrorAction Stop; Expand-Archive -Force (Join-Path $env:DL 'ReviewHunter.zip') $env:DL -ErrorAction Stop } catch { exit 1 }"
if not errorlevel 1 goto :downloaded
rem Private repository: the public URL answers 404; the GitHub CLI downloads with the user's login.
where gh >nul 2>nul
if errorlevel 1 goto :download_failed
echo   Public download refused: trying the GitHub CLI with your login...
gh release download --repo Andrianarivelo/ReviewHunter --pattern ReviewHunter.zip --dir "%DL%" --clobber
if errorlevel 1 goto :download_failed
powershell -NoProfile -ExecutionPolicy Bypass -Command "Expand-Archive -Force (Join-Path $env:DL 'ReviewHunter.zip') $env:DL"
:downloaded
set "INSTALLER="
for /f "delims=" %%F in ('dir /s /b "%DL%\reviewhunter_installer.py" 2^>nul') do set "INSTALLER=%%F"
if not defined INSTALLER goto :download_failed

rem ---------------------------------------------------------------- 3) run the installer
:run_installer
echo   Opening the installer window (follow the progress there)...
echo.
"%PY%" "%INSTALLER%" %*
set "RC=%errorlevel%"
if "%RC%"=="0" exit /b 0
echo.
echo   Setup did not finish (code %RC%). Log: %TEMP%\reviewhunter-install.log
pause
exit /b %RC%

:download_failed
echo   Download failed. Check your internet connection. If the repository is private,
echo   download ReviewHunter.zip from the Releases page while signed in to GitHub,
echo   or install the GitHub CLI and run "gh auth login".
pause
exit /b 1

rem ---------------------------------------------------------------- helpers
:find_python
rem The py launcher, then python on PATH (the Microsoft Store stub prints nothing), then per-user installs.
set "PY="
if "%RH_FORCE_UV%"=="1" exit /b 0
for /f "usebackq delims=" %%P in (`py -3 -c "%PROBE%" 2^>nul`) do set "PY=%%P"
if defined PY exit /b 0
for /f "usebackq delims=" %%P in (`python -c "%PROBE%" 2^>nul`) do set "PY=%%P"
if defined PY exit /b 0
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do (
    if exist "%%D\python.exe" for /f "usebackq delims=" %%P in (`call "%%D\python.exe" -c "%PROBE%" 2^>nul`) do set "PY=%%P"
)
exit /b 0

:private_python
rem uv (https://docs.astral.sh/uv) installs a standalone Python in the user profile. UV_NO_MODIFY_PATH
rem keeps the user's PATH untouched; ReviewHunter only needs the interpreter path.
set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not exist "%UV%" powershell -NoProfile -ExecutionPolicy Bypass -Command "$env:UV_NO_MODIFY_PATH='1'; [Net.ServicePointManager]::SecurityProtocol='Tls12'; irm https://astral.sh/uv/install.ps1 | iex" >nul 2>nul
if not exist "%UV%" exit /b 0
"%UV%" python install 3.12 >nul 2>nul
for /f "usebackq delims=" %%P in (`call "%UV%" python find 3.12 2^>nul`) do set "PY=%%P"
if not defined PY exit /b 0
set "UVPY=%PY%"
set "PY="
for /f "usebackq delims=" %%P in (`call "%UVPY%" -c "%PROBE%" 2^>nul`) do set "PY=%%P"
exit /b 0
