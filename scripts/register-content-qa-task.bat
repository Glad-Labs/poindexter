@echo off
REM Register the Glad Labs Content QA scheduled task (run as Administrator)
REM Runs every Sunday at 3:00 AM
schtasks /Create /TN "Glad Labs Content QA" /TR "\"C:\Users\mattm\AppData\Local\Programs\Python\Python312\pythonw.exe\" \"C:\Users\mattm\glad-labs-website\scripts\content-qa-checker.py\"" /SC WEEKLY /D SUN /ST 03:00 /F /RL HIGHEST
if %ERRORLEVEL% EQU 0 (
    echo Task registered successfully.
    schtasks /Query /TN "Glad Labs Content QA" /V /FO LIST
) else (
    echo Failed to register task. Try running as Administrator.
)
pause
