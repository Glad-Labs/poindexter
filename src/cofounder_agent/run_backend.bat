@echo off
REM Start the Co-Founder Agent backend
cd /d "c:\Users\mattm\glad-labs-website\src\cofounder_agent"
echo [+] Starting Glad Labs Co-Founder Agent...
echo     Port: 8001
echo     Task Executor: ENABLED
python -m uvicorn main:app --host 127.0.0.1 --port 8001 --reload
pause
