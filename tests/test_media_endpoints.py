#!/usr/bin/env python3
"""
Quick test script for media endpoints.

Tests:
1. Health check
2. Image search
3. Image generation

Usage:
  python test_media_endpoints.py
"""

import asyncio
import httpx
import json
from typing import Optional

BASE_URL = "http://localhost:8000"
API_TIMEOUT = 30


async def test_health_check():
    """Test the health endpoint"""
    print("\n" + "="*70)
    print("TEST 1: Health Check")
    print("="*70)
    
    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.get(f"{BASE_URL}/api/media/health")
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Health Check Passed")
                print(f"  Status: {data.get('status')}")
                print(f"  Pexels: {data.get('pexels_available')}")
                print(f"  SDXL: {data.get('sdxl_available')}")
                print(f"  Message: {data.get('message')}")
                return True
            else:
                print(f"❌ Health Check Failed: {response.status_code}")
                print(f"  Response: {response.text}")
                return False
    
    except Exception as e:
        print(f"❌ Health Check Error: {e}")
        return False


async def test_image_search(query: str = "artificial intelligence technology"):
    """Test the image search endpoint"""
    print("\n" + "="*70)
    print(f"TEST 2: Image Search - '{query}'")
    print("="*70)
    
    try:
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            params = {"query": query, "count": 1}
            response = await client.get(
                f"{BASE_URL}/api/media/images/search",
                params=params
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Image Search Passed")
                print(f"  Success: {data.get('success')}")
                print(f"  Image URL: {data.get('image_url')}")
                print(f"  Message: {data.get('message')}")
                
                if data.get('image'):
                    image = data['image']
                    print(f"  Source: {image.get('source')}")
                    print(f"  Photographer: {image.get('photographer')}")
                
                return data.get('success', False)
            else:
                print(f"❌ Image Search Failed: {response.status_code}")
                print(f"  Response: {response.text}")
                return False
    
    except Exception as e:
        print(f"❌ Image Search Error: {e}")
        return False


async def test_image_generation(prompt: str = "AI gaming NPCs futuristic"):
    """Test the image generation endpoint"""
    print("\n" + "="*70)
    print(f"TEST 3: Image Generation - '{prompt}'")
    print("="*70)
    
    try:
        payload = {
            "prompt": prompt,
            "title": "Test Article",
            "use_pexels": True,
            "use_generation": False,
        }
        
        print(f"Request payload:")
        print(json.dumps(payload, indent=2))
        
        async with httpx.AsyncClient(timeout=API_TIMEOUT) as client:
            response = await client.post(
                f"{BASE_URL}/api/media/generate-image",
                json=payload
            )
            
            if response.status_code == 200:
                data = response.json()
                print(f"✅ Image Generation Passed")
                print(f"  Success: {data.get('success')}")
                print(f"  Image URL: {data.get('image_url')}")
                print(f"  Time: {data.get('generation_time'):.2f}s")
                print(f"  Message: {data.get('message')}")
                
                if data.get('image'):
                    image = data['image']
                    print(f"  Source: {image.get('source')}")
                    print(f"  Photographer: {image.get('photographer')}")
                
                return data.get('success', False)
            else:
                print(f"❌ Image Generation Failed: {response.status_code}")
                print(f"  Response: {response.text}")
                return False
    
    except Exception as e:
        print(f"❌ Image Generation Error: {e}")
        return False


async def main():
    """Run all tests"""
    print("\n" + "="*70)
    print("🧪 MEDIA ENDPOINTS TEST SUITE")
    print("="*70)
    print(f"Base URL: {BASE_URL}")
    
    results = {}
    
    # Test 1: Health check
    results['health'] = await test_health_check()
    
    # Test 2: Image search
    results['search'] = await test_image_search()
    
    # Test 3: Image generation
    results['generation'] = await test_image_generation()
    
    # Summary
    print("\n" + "="*70)
    print("📊 TEST SUMMARY")
    print("="*70)
    
    for test_name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {test_name.upper():15} {status}")
    
    total_passed = sum(1 for v in results.values() if v)
    total_tests = len(results)
    print(f"\n  Total: {total_passed}/{total_tests} passed")
    
    if total_passed == total_tests:
        print("\n✅ All tests passed!")
    else:
        print(f"\n⚠️ {total_tests - total_passed} test(s) failed")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️ Tests interrupted by user")
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
