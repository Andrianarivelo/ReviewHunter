@echo off
rem Benchmarks J (Zotero connector vs previous importer) and K (shared-library concurrency), then figures.
setlocal
cd /d "%~dp0.."
".venv\Scripts\python.exe" "benchmarks\benchmark_zotero_shared.py" measure
if errorlevel 1 goto failed
if exist "%USERPROFILE%\miniconda3\python.exe" (
    "%USERPROFILE%\miniconda3\python.exe" "benchmarks\benchmark_zotero_shared.py" plot
) else (
    python "benchmarks\benchmark_zotero_shared.py" plot
)
if errorlevel 1 goto failed
echo Results saved in benchmarks\results\J_zotero_sync.* and K_shared_concurrency.*
pause
exit /b 0
:failed
echo Benchmark failed. Review the error above.
pause
exit /b 1
