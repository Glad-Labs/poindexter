# title: The human review queue and the gates behind it
# category: process
# font_size: 26
# description: Two commands telling one story — drafts waiting on operator
#   sign-off with their quality scores, then the QA reviewer chain that scored
#   them. Use for beats about human-in-the-loop approval, quality gates, or
#   what happens between "written" and "published".
#
# Deliberately ID-free. Drilling into one task (`pipeline qa <id>`) is the
# richer shot, but a hardcoded id rots the moment that task is archived and the
# clip would then bake into a "not found" error. Anything referencing a
# specific row belongs in a tape only once the id can be resolved at bake time.
#
# `pipeline list-paused` was the original opener and is NOT used: it renders
# "(no paused pipelines)" on a healthy install, and an empty state is not
# footage.

Type "poindexter tasks list --status awaiting_approval --limit 5"
Sleep 400ms
Enter
Wait+Screen@45s /Tasks:/
Sleep 5s

Type "clear"
Enter
Type "poindexter qa-gates list"
Sleep 400ms
Enter
Wait+Screen@45s /REVIEWER/
Sleep 5s
