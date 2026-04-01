"""Auto-publisher — validates, approves, and publishes tasks.

Runs the content_validator against each task's content BEFORE publishing.
Tasks that fail validation are rejected with feedback instead of published.
"""

import json
import os
import sys
import time
import urllib.request

# Add the backend to sys.path so we can import content_validator
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src", "cofounder_agent"))
# Ensure scripts/ is on sys.path so `from lib.…` works
sys.path.insert(0, os.path.dirname(__file__))

from services.content_validator import validate_content  # noqa: E402
from lib.config import load_api_token  # noqa: E402

API_URL = "https://cofounder-production.up.railway.app"
API_TOKEN = load_api_token()
AUTH = f"Bearer {API_TOKEN}"


def get_task_content(task_id: str) -> dict:
    """Fetch full task details including content."""
    try:
        req = urllib.request.Request(
            f"{API_URL}/api/tasks/{task_id}",
            headers={"Authorization": AUTH},
        )
        return json.loads(urllib.request.urlopen(req, timeout=10).read())
    except Exception:
        return {}


def reject_task(task_id: str, reason: str):
    """Reject a task with feedback."""
    try:
        payload = json.dumps({"reason": reason}).encode()
        req = urllib.request.Request(
            f"{API_URL}/api/tasks/{task_id}/reject",
            data=payload,
            method="POST",
            headers={"Authorization": AUTH, "Content-Type": "application/json"},
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass  # Reject endpoint may not exist yet — task stays in awaiting_approval


def approve_and_publish():
    """Validate, approve, and publish tasks. Reject those that fail validation."""
    published = 0
    rejected = 0

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
            topic = t.get("topic", "")
            title = t.get("task_name", topic)

            # Fetch full content for validation
            full_task = get_task_content(tid)
            content = ""
            result = full_task.get("result")
            if isinstance(result, dict):
                content = result.get("content", "")
            if not content:
                content = full_task.get("content", "")

            # Validate content
            if content:
                validation = validate_content(title, content, topic)
                if not validation.passed:
                    issues_text = "; ".join(
                        f"[{i.category}] {i.description}" for i in validation.issues[:3]
                    )
                    print(f"REJECTED: {topic[:50]} — {issues_text}")
                    reject_task(tid, f"Content validation failed: {issues_text}")
                    rejected += 1
                    time.sleep(0.3)
                    continue

            # Approve + publish
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

    return published, rejected


if __name__ == "__main__":
    pub, rej = approve_and_publish()
    if pub > 0 or rej > 0:
        print(f"Published: {pub}, Rejected: {rej}")
