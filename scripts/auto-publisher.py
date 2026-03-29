"""Auto-publisher — approves and publishes tasks that score above threshold.

Run periodically via Task Scheduler. One-shot: checks once and exits.
"""

import json
import urllib.request
import time

API_URL = "https://cofounder-production.up.railway.app"
API_TOKEN = "REDACTED_API_TOKEN"
AUTH = f"Bearer {API_TOKEN}"


def approve_and_publish():
    """Approve and publish all awaiting_approval and approved tasks."""
    published = 0
    for status in ["awaiting_approval", "approved"]:
        try:
            req = urllib.request.Request(
                f"{API_URL}/api/tasks?status={status}&limit=30",
                headers={"Authorization": AUTH},
            )
            data = json.loads(urllib.request.urlopen(req, timeout=10).read())
            tasks = data.get("tasks", [])
        except Exception:
            continue

        for t in tasks:
            tid = t["task_id"]
            try:
                if status == "awaiting_approval":
                    urllib.request.urlopen(
                        urllib.request.Request(
                            f"{API_URL}/api/tasks/{tid}/approve",
                            method="POST",
                            headers={"Authorization": AUTH},
                        ),
                        timeout=10,
                    )
                urllib.request.urlopen(
                    urllib.request.Request(
                        f"{API_URL}/api/tasks/{tid}/publish",
                        method="POST",
                        headers={"Authorization": AUTH},
                    ),
                    timeout=10,
                )
                published += 1
            except Exception:
                pass
            time.sleep(0.3)

    return published


if __name__ == "__main__":
    count = approve_and_publish()
    if count > 0:
        print(f"Published {count} posts")
