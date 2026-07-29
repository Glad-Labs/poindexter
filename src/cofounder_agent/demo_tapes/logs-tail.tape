# title: Live log stream from the worker
# category: process
# font_size: 24
# description: A live tail of the worker's log stream, then the brain daemon's —
#   real request and job lines scrolling past. Genuine motion rather than a
#   static table. Use for beats about the system running unattended,
#   observability, or "here is what it does while nobody is watching."
#
# Levels are colour-coded only where Loki carries a `level` label. The
# structured worker/brain logs do; uvicorn's access-log stream does not, so
# a share of these lines render with an empty level column. That is honest
# output, not a bug — do not describe this clip as "colour-coded by level"."

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
