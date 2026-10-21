@echo off
REM Best-effort wrapper: re-run the agent-memory mirror after changes.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0sync-agent-memory.ps1" %*
