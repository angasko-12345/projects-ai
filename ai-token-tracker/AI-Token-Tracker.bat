@echo off
cd /d "%~dp0"
where pythonw >nul 2>nul
if %errorlevel%==0 (
    start "" pythonw -m token_tracker
) else (
    start "" python -m token_tracker
)
