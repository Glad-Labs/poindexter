#!/usr/bin/env python3
import jwt
from datetime import datetime, timedelta
import requests
import json

secret = "dev-jwt-secret-change-in-production"
payload = {
    "sub": "dev-user",
    "type": "access",
    "iat": int(datetime.now().timestamp()),
    "exp": int((datetime.now() + timedelta(days=30)).timestamp())
}

token = jwt.encode(payload, secret, algorithm="HS256")
headers = {"Authorization": f"Bearer {token}"}

try:
    response = requests.get("http://localhost:8000/api/tasks?offset=0&limit=5", headers=headers, timeout=5)
    data = response.json()
    
    if 'results' in data and data['results']:
        task = data['results'][0]
        print(f"Latest Task ID: {task.get('task_id')}")
        print(f"Status: {task.get('status')}")
        print(f"Content available: {'Yes' if task.get('content_result') else 'No'}")
        if task.get('content_result'):
            print(f"Content preview: {str(task.get('content_result'))[:200]}")
    else:
        print("No tasks found")
except Exception as e:
    print(f"Error: {e}")
