@echo off
rem Read the annotated library papers without edits, then render the accuracy figure.
setlocal
cd /d "%~dp0.."
".venv\Scripts\python.exe" "benchmarks\benchmark_methodology.py" measure %*
if errorlevel 1 goto failed
if exist "%USERPROFILE%\miniconda3\python.exe" (
    "%USERPROFILE%\miniconda3\python.exe" "benchmarks\benchmark_methodology.py" plot %*
) else (
    python "benchmarks\benchmark_methodology.py" plot %*
)
if errorlevel 1 goto failed
echo Results saved in benchmarks\results\I_methodology.*
pause
exit /b 0
:failed
echo Benchmark failed. Review the error above.
pause
exit /b 1
