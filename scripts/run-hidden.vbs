' Run a command completely hidden - no window flash at all
' Usage: wscript run-hidden.vbs "powershell -File script.ps1"
Set objShell = CreateObject("WScript.Shell")
objShell.Run WScript.Arguments(0), 0, False
