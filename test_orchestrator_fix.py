#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import jwt
from datetime import datetime, timedelta
import requests
import time

secret = "dev-jwt-secret-change-in-production"
payload = {
    "sub": "dev-user",
    "type": "access",
    "iat": int(datetime.now().timestamp()),
    "exp": int((datetime.now() + timedelta(days=30)).timestamp())
}

token = jwt.encode(payload, secret, algorithm="HS256")
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Create a content task
data = {
    "title": "The Future of AI Ethics",
    "topic": "How organizations are addressing ethical concerns in artificial intelligence development",
    "content_type": "article"
}

try:
    response = requests.post("http://localhost:8000/api/content/tasks", 
                            json=data, headers=headers, timeout=5)
    if response.status_code == 201:
        task_id = response.json()['task_id']
        print(f"Task created: {task_id}")
        print(f"Status: {response.json().get('status')}")
        
        # Wait a moment then check status
        time.sleep(3)
        
        resp = requests.get(f"http://localhost:8000/api/tasks/{task_id}", headers=headers, timeout=5)
        if resp.status_code == 200:
            task_data = resp.json()
            print(f"\nTask Status Check:")
            print(f"Status: {task_data.get('status')}")
            print(f"Has content: {'Yes' if task_data.get('content_result') else 'No'}")
            if task_data.get('content_result'):
                content = task_data.get('content_result')
                if isinstance(content, dict):
                    print(f"Content is dict with keys: {list(content.keys())}")
                else:
                    print(f"Content preview: {str(content)[:150]}...")
        else:
            print(f"Failed to get task: {resp.status_code}")
    else:
        print(f"Task creation failed: {response.status_code}")
        print(f"Response: {response.text[:200]}")
except Exception as e:
    print(f"Error: {e}")
