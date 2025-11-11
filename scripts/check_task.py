#!/usr/bin/env python3
import time
import requests

# Wait for content generation
print("⏳ Waiting 17 seconds for content generation...")
time.sleep(17)

# Check task
task_id = '172f2421-a994-4733-af73-bc9db722e8cf'
r = requests.get(f'http://localhost:8000/api/tasks/{task_id}')
data = r.json()

print(f"✅ Task Status: {data['status']}")
print(f"✅ Has Content: {len(data.get('result', '')) > 0}")
print(f"✅ Content Length: {len(data.get('result', ''))} characters")
if len(data.get('result', '')) > 0:
    print(f"✅ Preview: {data.get('result', '')[:100]}...")
