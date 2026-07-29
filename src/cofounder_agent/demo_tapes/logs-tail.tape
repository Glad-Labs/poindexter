# title: Live log stream from the worker
# category: process
# font_size: 24
# description: A live tail of the worker's log stream — lines arriving in real
#   time, colour-coded by level. Genuine motion rather than a static table. Use
#   for beats about the system running unattended, observability, or "here is
#   what it does while nobody is watching."

Type "poindexter logs --service worker --limit 12 --since 2h"
Sleep 400ms
Enter
Wait+Screen@45s /Logs:/
Sleep 4s

Type "poindexter logs --service brain-daemon --limit 8 --since 2h"
Sleep 400ms
Enter
Wait+Screen@45s /Logs:|no log lines/
Sleep 5s
