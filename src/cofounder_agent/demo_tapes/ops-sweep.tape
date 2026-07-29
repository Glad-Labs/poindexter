# title: An operator health sweep
# category: process
# font_size: 26
# description: Four checks run back to back the way an operator actually starts
#   a session — health score, spend against budget, worker state, then the task
#   queue. Longer multi-command shot with a real rhythm. Use for beats about
#   running the business, monitoring, or the daily routine.

Type "poindexter doctor"
Sleep 400ms
Enter
Wait+Screen@60s /Health score/
Sleep 4s

Type "clear"
Enter
Type "poindexter costs budget"
Sleep 400ms
Enter
Wait+Screen@45s /amount_remaining/
Sleep 3s

Type "poindexter costs operational"
Sleep 400ms
Enter
Wait+Screen@45s /\$/
Sleep 4s
