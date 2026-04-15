@echo off
REM Wrapper to run a Claude Code session from Task Scheduler
REM Usage: run-claude-session.cmd <session-name>
powershell.exe -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0claude-sessions.ps1" -Session %1
