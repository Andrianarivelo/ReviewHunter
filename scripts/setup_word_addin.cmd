@echo off
rem ---------------------------------------------------------------------------
rem  ReviewHunter Word add-in setup (double-click me).
rem
rem  Finds the certificate and manifest that ReviewHunter generated in the library
rem  folder (REVIEWHUNTER_HOME, default %USERPROFILE%\ReviewHunter) and runs
rem  setup_word_addin.ps1 with them. Extra arguments are passed through, so
rem  "setup_word_addin.cmd -Uninstall" removes the add-in again.
rem ---------------------------------------------------------------------------
setlocal

set "RH_LIB=%REVIEWHUNTER_HOME%"
if "%RH_LIB%"=="" set "RH_LIB=%USERPROFILE%\ReviewHunter"

set "RH_CERT=%RH_LIB%\certs\reviewhunter-ca.crt"
set "RH_MANIFEST=%RH_LIB%\word_addin\manifest.xml"

if /I "%~1"=="-Uninstall" goto run

if not exist "%RH_CERT%" goto missing
if not exist "%RH_MANIFEST%" goto missing
goto run

:missing
echo.
echo  ReviewHunter has not prepared the Word add-in files yet.
echo  Expected:
echo    %RH_CERT%
echo    %RH_MANIFEST%
echo.
echo  Start ReviewHunter once (it creates them), then run this file again.
echo.
pause
exit /b 1

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup_word_addin.ps1" -CaCert "%RH_CERT%" -Manifest "%RH_MANIFEST%" %*
echo.
pause
endlocal
