@echo off
rem Run isolated identity checks, then render the three figure formats.
setlocal
cd /d "%~dp0.."
".venv\Scripts\python.exe" "benchmarks\benchmark_pdf_identity.py" measure
if errorlevel 1 goto failed
if exist "%USERPROFILE%\miniconda3\python.exe" (
    "%USERPROFILE%\miniconda3\python.exe" "benchmarks\benchmark_pdf_identity.py" plot
) else (
    python "benchmarks\benchmark_pdf_identity.py" plot
)
if errorlevel 1 goto failed
echo Results saved in benchmarks\results\H_pdf_identity.*
pause
exit /b 0
:failed
echo Benchmark failed. Review the error above.
pause
exit /b 1
