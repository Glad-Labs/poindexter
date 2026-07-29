# title: From queue to published post
# category: process
# font_size: 26
# description: The content lifecycle in three commands — what is queued, what is
#   in flight, what shipped. Longer shot that reads as a progression rather than
#   a single snapshot. Use for beats about the pipeline end to end.

Type "poindexter tasks list --limit 6"
Sleep 400ms
Enter
Wait+Screen@45s /Tasks:/
Sleep 4s

Type "clear"
Enter
Type "poindexter posts list --limit 6"
Sleep 400ms
Enter
Wait+Screen@45s /total/
Sleep 4s

Type "poindexter memory status"
Sleep 400ms
Enter
Wait+Screen@45s /by source_table/
Sleep 4s
