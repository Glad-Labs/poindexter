# title: The content pipeline task queue
# category: pipeline
# description: Live pipeline queue showing tasks in flight, awaiting approval, and published, colour-coded by state. Use for beats about the pipeline working or human review gates.

Type "poindexter tasks list --limit 6"
Sleep 400ms
Enter
Wait+Screen@45s /total/
Sleep 2500ms
