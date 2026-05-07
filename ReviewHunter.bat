@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 run.py %*
    goto :end
)

where python >nul 2>nul
if %errorlevel%==0 (
    python run.py %*
    goto :end
)

echo.
echo  ReviewHunter needs Python 3.10 or newer.
echo  Install it from https://www.python.org/downloads/  ^(tick "Add to PATH"^)
echo.
pause
exit /b 1

:end
if errorlevel 1 pause
