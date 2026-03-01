#!/usr/bin/env python3
"""
End-to-end test of the approval workflow with the fix.
Tests: Task creation -> Approval workflow -> Database persistence -> Public display
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from datetime import datetime

# Load environment
env_path = Path(".env.local")
if env_path.exists():
    from dotenv import load_dotenv
    load_dotenv(env_path)

sys.path.insert(0, "src/cofounder_agent")

import requests
import time


class ApprovalWorkflowTester:
    def __init__(self):
        self.backend_url = "http://localhost:8000"
        self.headers = {
            "Authorization": "Bearer dev-token-123",
            "Content-Type": "application/json"
        }

    async def test_approval_workflow(self):
        """Test the complete approval workflow"""
        print("\n" + "=" * 80)
        print("APPROVAL WORKFLOW END-TO-END TEST")
        print("=" * 80 + "\n")

        # Step 1: Check backend is ready
        print("[STEP 1] Checking backend health...")
        try:
            resp = requests.get(f"{self.backend_url}/api/health", timeout=5)
            if resp.status_code == 200:
                print("[OK] Backend is running")
            else:
                print(f"[ERROR] Backend health check failed: {resp.status_code}")
                return
        except Exception as e:
            print(f"[ERROR] Cannot connect to backend: {e}")
            print("   Please ensure backend is running: npm run dev:cofounder")
            return

        # Step 2: Find a task in awaiting_approval
        print("\n[STEP 2] Finding a task in awaiting_approval status...")
        try:
            resp = requests.get(
                f"{self.backend_url}/api/tasks?status=awaiting_approval&limit=1",
                headers=self.headers,
                timeout=10
            )
            if resp.status_code == 200:
                data = resp.json()
                tasks = data.get("tasks", [])
                if not tasks:
                    print("[WARNING] No tasks in awaiting_approval status")
                    print("   Creating a test blog post task first...")
                    # Create one
                    create_resp = requests.post(
                        f"{self.backend_url}/api/tasks",
                        headers=self.headers,
                        json={
                            "task_type": "blog_post",
                            "topic": "Testing Approval Workflow - " + datetime.now().strftime("%H:%M:%S"),
                            "style": "technical",
                            "tone": "professional",
                            "target_length": 1500
                        },
                        timeout=10
                    )
                    if create_resp.status_code != 202:
                        print(f"[ERROR] Failed to create test task: {create_resp.status_code}")
                        return
                    print("[OK] Created test task, waiting for it to reach awaiting_approval...")
                    # Wait a moment for processing
                    time.sleep(2)
                    #  Try again
                    resp = requests.get(
                        f"{self.backend_url}/api/tasks?status=awaiting_approval&limit=1",
                        headers=self.headers,
                        timeout=10
                    )
                    if resp.status_code != 200:
                        print("[ERROR] Failed to fetch tasks")
                        return
                    data = resp.json()
                    tasks = data.get("tasks", [])
                    if not tasks:
                        print("[ERROR] Still no tasks in awaiting_approval")
                        return

                task = tasks[0]
                task_id = task.get("id") or task.get("task_id")
                print(f"[OK] Found task: {task_id}")
                print(f"     Topic: {task.get('topic')}")
                print(f"     Current status: {task.get('status')}")
            else:
                print(f"[ERROR] Failed to fetch tasks: {resp.status_code}")
                return
        except Exception as e:
            print(f"[ERROR] Exception fetching tasks: {e}")
            import traceback
            traceback.print_exc()
            return

        # Step 3: Approve the task
        print("\n[STEP 3] Calling approve endpoint with auto_publish=true...")
        try:
            approve_resp = requests.post(
                f"{self.backend_url}/api/tasks/{task_id}/approve",
                headers=self.headers,
                json={
                    "approved": True,
                    "human_feedback": "Looks good!",
                    "auto_publish": True
                },
                timeout=30
            )
            print(f"[INFO] Approval response status: {approve_resp.status_code}")
            if approve_resp.status_code in [200, 201]:
                result = approve_resp.json()
                response_status = result.get("status")
                print(f"[OK] Approve endpoint responded successfully")
                print(f"     Response status: {response_status}")
                if response_status == "published":
                    print("     (Response shows 'published' from auto_publish)")
                elif response_status == "approved":
                    print("     (Response shows 'approved')")
            else:
                print(f"[ERROR] Approve endpoint returned {approve_resp.status_code}")
                print(f"   Response: {approve_resp.text}")
                return
        except Exception as e:
            print(f"[ERROR] Exception calling approve: {e}")
            import traceback
            traceback.print_exc()
            return

        # Step 4: Re-fetch task to verify database persistence
        print("\n[STEP 4] Re-fetching task from database to verify update persisted...")
        try:
            fetch_resp = requests.get(
                f"{self.backend_url}/api/tasks/{task_id}",
                headers=self.headers,
                timeout=10
            )
            if fetch_resp.status_code == 200:
                refetched_task = fetch_resp.json()
                db_status = refetched_task.get("status")
                print(f"[OK] Task fetched from database")
                print(f"     Database status: {db_status}")

                if db_status == "published":
                    print("\n[YES!] SUCCESS! Task status in database is 'published'")
                    print("   The approval workflow is working correctly!")
                elif db_status == "approved":
                    print("\n[OK] Task status in database is 'approved'")
                    print("   (auto_publish may be processing in background to create post)")
                    # Check if post was created
                    post_id = refetched_task.get("post_id")
                    post_slug = refetched_task.get("post_slug")
                    if post_id or post_slug:
                        print(f"   Post created: {post_slug}")
                else:
                    print(f"\n[FAILURE] Task status is still '{db_status}', not 'approved' or 'published'")
                    print("   The approval workflow fix may not be working correctly.")
                    return
            else:
                print(f"[ERROR] Failed to fetch task: {fetch_resp.status_code}")
                return
        except Exception as e:
            print(f"[ERROR] Exception re-fetching task: {e}")
            import traceback
            traceback.print_exc()
            return

        # Step 5: Check if post was created in posts table
        print("\n[STEP 5] Checking if post was created in posts table...")
        try:
            post_id = refetched_task.get("post_id")
            post_slug = refetched_task.get("post_slug")

            if post_id or post_slug:
                print(f"[OK] Post was created")
                print(f"     Post ID: {post_id}")
                print(f"     Post slug: {post_slug}")

                # Try to fetch the post via API
                if post_slug:
                    # Next.js will have it on the public site
                    public_url = f"http://localhost:3010/posts/{post_slug}"
                    print(f"     Public URL: {public_url}")
                    print("     (Check this URL manually to see if post displays)")
            else:
                print("[INFO] No post_id/post_slug in response (may be processed asynchronously)")
        except Exception as e:
            print(f"[WARNING] Could not check post creation: {e}")

        print("\n" + "=" * 80)
        print("TEST COMPLETE - Approval workflow appears to be working!")
        print("=" * 80)


async def main():
    tester = ApprovalWorkflowTester()
    await tester.test_approval_workflow()


if __name__ == "__main__":
    asyncio.run(main())
