@echo off
setlocal
cd /d "%~dp0"
if exist "runtime\python.exe" (
    "runtime\python.exe" -B bazaar_buyer.py
    goto done
)
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" bazaar_buyer.py
    goto done
)
where py.exe >nul 2>&1
if not errorlevel 1 (
    py -3 bazaar_buyer.py
    goto done
)
where python.exe >nul 2>&1
if not errorlevel 1 (
    python bazaar_buyer.py
    goto done
)
echo Python was not found. See the Installation section in README.md.
:done
pause
