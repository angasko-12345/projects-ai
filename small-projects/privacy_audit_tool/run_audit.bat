@echo off
setlocal
cd /d "%~dp0"
py -m privacy_audit.main %*
endlocal
