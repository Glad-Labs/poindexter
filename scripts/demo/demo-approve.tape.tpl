# Segment C — the approval moment. Template: build-demo.sh replaces
# __TASK_ID__ with the real id from segment A, renders work/demo-approve.tape.

Output work/segment-c-approve.mp4

Set Shell "bash"
Set FontSize 20
Set Width 1200
Set Height 560
Set Padding 24
Set Theme { "name": "poindexter", "background": "#0d1117", "foreground": "#c9d5e1", "cursor": "#00e5ff", "black": "#0d1117", "red": "#ff7b72", "green": "#3fdf8f", "yellow": "#febc2e", "blue": "#58a6ff", "magenta": "#bc8cff", "cyan": "#00e5ff", "white": "#c9d5e1", "brightBlack": "#5b6b7c", "brightRed": "#ff7b72", "brightGreen": "#3fdf8f", "brightYellow": "#febc2e", "brightBlue": "#58a6ff", "brightMagenta": "#bc8cff", "brightCyan": "#00e5ff", "brightWhite": "#f2f7fa" }
Set TypingSpeed 55ms

Hide
Type "clear && export PS1='$ '"
Enter
Sleep 500ms
Show

Sleep 1s
Type "poindexter tasks list --status awaiting_approval"
Sleep 400ms
Enter
# The queue with QA scores — the money shot; let it breathe
Sleep 5s
Type "poindexter tasks approve __TASK_ID__"
Sleep 500ms
Enter
Sleep 5s
