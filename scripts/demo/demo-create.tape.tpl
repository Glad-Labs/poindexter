# Segment A — queue a topic. Template: build-demo.sh replaces __DEMO_TOPIC__
# with the literal topic so viewers see real words typed, then renders the
# generated work/demo-create.tape with vhs.

Output work/segment-a-create.mp4

Set Shell "bash"
Set FontSize 20
Set Width 1200
Set Height 480
Set Padding 24
Set Theme { "name": "poindexter", "background": "#0d1117", "foreground": "#c9d5e1", "cursor": "#00e5ff", "black": "#0d1117", "red": "#ff7b72", "green": "#3fdf8f", "yellow": "#febc2e", "blue": "#58a6ff", "magenta": "#bc8cff", "cyan": "#00e5ff", "white": "#c9d5e1", "brightBlack": "#5b6b7c", "brightRed": "#ff7b72", "brightGreen": "#3fdf8f", "brightYellow": "#febc2e", "brightBlue": "#58a6ff", "brightMagenta": "#bc8cff", "brightCyan": "#00e5ff", "brightWhite": "#f2f7fa" }
Set TypingSpeed 55ms

Hide
Type "clear && export PS1='$ '"
Enter
Sleep 500ms
Show

Sleep 1s
Type `poindexter tasks create "__DEMO_TOPIC__"`
Sleep 500ms
Enter
# Let the CLI print its acknowledgement (task id + queued state)
Sleep 6s
