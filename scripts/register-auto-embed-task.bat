@echo off
REM Register the Glad Labs Auto-Embed scheduled task (run as Administrator)
schtasks /Create /TN "Glad Labs Auto-Embed" /TR "\"C:\Users\mattm\AppData\Local\Programs\Python\Python312\pythonw.exe\" \"C:\Users\mattm\glad-labs-website\scripts\auto-embed.py\"" /SC HOURLY /MO 1 /F /RL HIGHEST /ST 00:00
if %ERRORLEVEL% EQU 0 (
    echo Task registered successfully.
    schtasks /Query /TN "Glad Labs Auto-Embed" /V /FO LIST
) else (
    echo Failed to register task. Try running as Administrator.
)
pause
