@echo off
:: Poindexter Recovery Agent (port 9841) — receives POST /recover from the
:: brain daemon (Docker container) and runs host-level recovery actions:
:: Scheduled-Task restarts and a compose reapply (start-stack.sh up -d).
:: Started windowless at logon by the "Poindexter Recovery Agent" Task.

set "LOG=%USERPROFILE%\.poindexter\logs\recovery-agent.log"
if not exist "%USERPROFILE%\.poindexter\logs" mkdir "%USERPROFILE%\.poindexter\logs"

echo %date% %time% Starting Poindexter Recovery Agent >> "%LOG%"
:: %~dp0 = this script's own dir, so we run the sibling recovery-agent.py
:: whether this lives in ~/.poindexter/scripts or the auto-synced deploy clone.
python "%~dp0recovery-agent.py" >> "%LOG%" 2>&1
