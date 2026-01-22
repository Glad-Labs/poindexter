#!/usr/bin/env python3
"""
Test script to debug POST auth issues
"""
import jwt
import json
from datetime import datetime, timedelta
import requests

# Development JWT secret (must match backend)
DEV_SECRET = 'dev-jwt-secret-change-in-production-to-random-64-chars'

# Create token
payload = {
    'sub': 'dev-user',
    'user_id': 'dev-user-id',
    'email': 'dev@example.com',
    'username': 'dev-user',
    'auth_provider': 'mock',
    'type': 'access',
    'iat': datetime.utcnow(),
    'exp': datetime.utcnow() + timedelta(hours=24),
}

token = jwt.encode(payload, DEV_SECRET, algorithm='HS256')
print(f"✅ Generated token: {token[:50]}...")

# Test GET request
print("\n📝 Testing GET /api/tasks...")
headers = {'Authorization': f'Bearer {token}'}
response = requests.get('http://localhost:8000/api/tasks', headers=headers)
print(f"Status: {response.status_code}")
if response.status_code != 200:
    print(f"Response: {response.text[:200]}")

# Test POST request
print("\n📝 Testing POST /api/tasks/1/approve...")
response = requests.post(
    'http://localhost:8000/api/tasks/1/approve',
    headers=headers,
    json={'feedback': 'test feedback'}
)
print(f"Status: {response.status_code}")
if response.status_code != 200:
    print(f"Response: {response.text[:300]}")
