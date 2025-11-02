#!/usr/bin/env python
"""Test script to POST to /api/chat endpoint"""
import requests
import json

# Wait a moment for server to be ready
import time
time.sleep(2)

url = "http://127.0.0.1:8000/api/chat"
payload = {
    "message": "What is 2+2?",
    "model": "ollama",
    "conversationId": "test123"
}

print(f"[TEST] Sending POST to {url}")
print(f"[TEST] Payload: {json.dumps(payload, indent=2)}")

try:
    response = requests.post(url, json=payload, timeout=30)
    print(f"[TEST] Status Code: {response.status_code}")
    print(f"[TEST] Response:")
    print(json.dumps(response.json(), indent=2))
except Exception as e:
    print(f"[TEST] ERROR: {type(e).__name__}: {e}")
