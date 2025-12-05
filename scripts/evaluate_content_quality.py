import requests
import time
import json
import sys

API_URL = "http://localhost:8000"

def evaluate_content_generation(topic="The Future of AI Agents"):
    print(f"🧪 Starting evaluation for topic: '{topic}'")
    
    # 1. Trigger Content Generation
    payload = {
        "topic": topic,
        "content_type": "blog_post",
        "target_audience": "Developers",
        "tone": "Professional"
    }
    
    try:
        print("   Sending request to /api/content/generate-blog-post...")
        response = requests.post(f"{API_URL}/api/content/generate-blog-post", json=payload)
        response.raise_for_status()
        task_data = response.json()
        task_id = task_data.get("task_id")
        print(f"   ✅ Task started. ID: {task_id}")
    except Exception as e:
        print(f"   ❌ Failed to start task: {e}")
        return

    # 2. Poll for Completion
    print("   ⏳ Waiting for completion (this may take a minute)...")
    max_retries = 60
    for i in range(max_retries):
        try:
            status_response = requests.get(f"{API_URL}/api/tasks/{task_id}")
            status_response.raise_for_status()
            status_data = status_response.json()
            status = status_data.get("status")
            
            if status == "completed":
                print(f"   ✅ Task completed!")
                result = status_data.get("result", {})
                evaluate_result(result)
                return
            elif status == "failed":
                print(f"   ❌ Task failed: {status_data.get('error')}")
                return
            
            print(f"      Status: {status} ({i}/{max_retries})")
            time.sleep(2)
        except Exception as e:
            print(f"   ⚠️ Error checking status: {e}")
            time.sleep(2)

    print("   ❌ Timeout waiting for task completion.")

def evaluate_result(result):
    print("\n📊 Evaluation Results:")
    
    content = result.get("content", "")
    if not content:
        print("   ❌ No content generated.")
        return

    # Metric 1: Length
    word_count = len(content.split())
    print(f"   - Word Count: {word_count} (Target: >500)")
    if word_count > 500:
        print("     ✅ PASS")
    else:
        print("     ⚠️ FAIL (Too short)")

    # Metric 2: Structure (Markdown headers)
    has_headers = "# " in content
    print(f"   - Markdown Structure: {'Present' if has_headers else 'Missing'}")
    if has_headers:
        print("     ✅ PASS")
    else:
        print("     ⚠️ FAIL")

    # Metric 3: Metadata
    metadata = result.get("metadata", {})
    print(f"   - Metadata: {list(metadata.keys())}")
    if "title" in metadata and "seo_keywords" in metadata:
        print("     ✅ PASS")
    else:
        print("     ⚠️ FAIL")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        topic = sys.argv[1]
    else:
        topic = "The Future of AI Agents"
    
    evaluate_content_generation(topic)
