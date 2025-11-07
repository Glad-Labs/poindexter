#!/usr/bin/env python3
"""Test creating a post in Strapi directly"""

import httpx
import asyncio
import json
import os
from dotenv import load_dotenv

# Load environment
load_dotenv('.env.local', override=True)

STRAPI_URL = os.getenv('STRAPI_URL', 'http://localhost:1337')
STRAPI_TOKEN = os.getenv('STRAPI_API_TOKEN', '')

print(f"Strapi URL: {STRAPI_URL}")
print(f"Token: {STRAPI_TOKEN[:20]}...")

async def test_api():
    headers = {
        "Authorization": f"Bearer {STRAPI_TOKEN}",
        "Content-Type": "application/json"
    }
    
    async with httpx.AsyncClient() as client:
        # Test 1: Get posts
        print("\n1. Testing GET /api/posts...")
        try:
            resp = await client.get(f"{STRAPI_URL}/api/posts", headers=headers, timeout=5)
            print(f"  Status: {resp.status_code}")
            if resp.status_code == 200:
                data = resp.json()
                print(f"  ✅ Posts: {len(data.get('data', []))} posts found")
            else:
                print(f"  ❌ Error: {resp.text[:200]}")
        except Exception as e:
            print(f"  ❌ Exception: {e}")
        
        # Test 2: Create a post
        print("\n2. Testing POST /api/posts (create)...")
        post_data = {
            "data": {
                "title": "Test Post from Python",
                "slug": "test-post-python",
                "content": "This is a test post created from Python",
                "excerpt": "Test",
                "publishedAt": "2025-11-06T00:00:00Z"
            }
        }
        try:
            resp = await client.post(
                f"{STRAPI_URL}/api/posts",
                json=post_data,
                headers=headers,
                timeout=5
            )
            print(f"  Status: {resp.status_code}")
            if resp.status_code in [200, 201]:
                data = resp.json()
                print(f"  ✅ Post created: ID={data.get('data', {}).get('id')}")
                print(f"  Response: {json.dumps(data, indent=2)[:300]}...")
            else:
                print(f"  ❌ Error: {resp.text[:500]}")
        except Exception as e:
            print(f"  ❌ Exception: {e}")

if __name__ == "__main__":
    asyncio.run(test_api())
