#!/usr/bin/env python
"""
Poindexter API Verification Script
Tests all newly created endpoints
"""

import requests
import json
from datetime import datetime

BASE_URL = "http://localhost:8000"

def print_header(title):
    """Print formatted header"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")

def test_endpoint(method, endpoint, name, data=None):
    """Test a single endpoint"""
    url = f"{BASE_URL}{endpoint}"
    response = None
    try:
        if method == "GET":
            response = requests.get(url, timeout=5)
        elif method == "POST":
            response = requests.post(url, json=data, timeout=5)
        elif method == "DELETE":
            response = requests.delete(url, timeout=5)
        
        if response is not None:
            status = "✅" if response.status_code < 400 else "❌"
            print(f"{status} {method:6} {endpoint:40} [{response.status_code}] {name}")
            
            if response.status_code >= 400:
                print(f"   └─ Error: {response.text[:100]}")
            
            return response.status_code < 400
        else:
            print(f"❌ {method:6} {endpoint:40} [ERROR] No response")
            return False
    
    except requests.exceptions.ConnectionError:
        print(f"❌ {method:6} {endpoint:40} [OFFLINE] {name}")
        return False
    except Exception as e:
        print(f"❌ {method:6} {endpoint:40} [ERROR] {str(e)[:50]}")
        return False

def main():
    """Run all tests"""
    print("\n🤖 Poindexter API Verification")
    print(f"Testing: {BASE_URL}")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    results = {
        "health": [],
        "social": [],
        "models": [],
        "metrics": [],
    }
    
    # Health check
    print_header("Health Checks")
    results["health"].append(test_endpoint("GET", "/api/health", "API Health"))
    results["health"].append(test_endpoint("GET", "/metrics/health", "Metrics Health"))
    
    # Social Media Endpoints
    print_header("Social Media Endpoints")
    results["social"].append(test_endpoint("GET", "/api/social/platforms", "Get Platforms"))
    results["social"].append(test_endpoint("GET", "/api/social/posts", "Get Posts"))
    results["social"].append(test_endpoint("GET", "/api/social/trending?platform=twitter", "Get Trending"))
    
    # Model Endpoints
    print_header("Model Endpoints")
    results["models"].append(test_endpoint("GET", "/api/models", "Get Models (legacy)"))
    results["models"].append(test_endpoint("GET", "/api/v1/models/available", "Get Models (v1)"))
    
    # Metrics Endpoints
    print_header("Metrics Endpoints")
    results["metrics"].append(test_endpoint("GET", "/api/metrics", "Get Metrics"))
    results["metrics"].append(test_endpoint("GET", "/api/metrics/costs", "Get Costs"))
    results["metrics"].append(test_endpoint("GET", "/api/metrics/summary", "Get Summary"))
    
    # Summary
    print_header("Test Summary")
    total_passed = sum(sum(1 for r in group if r) for group in results.values())
    total_tests = sum(len(group) for group in results.values())
    
    for group_name, group_results in results.items():
        passed = sum(1 for r in group_results if r)
        total = len(group_results)
        status = "✅" if passed == total else "⚠️"
        print(f"{status} {group_name.upper():10} {passed}/{total} passed")
    
    print(f"\n📊 Overall: {total_passed}/{total_tests} tests passed")
    
    if total_passed == total_tests:
        print("\n🎉 All tests passed! Poindexter is ready to go!")
        return 0
    else:
        print(f"\n⚠️  {total_tests - total_passed} test(s) failed. Check the errors above.")
        return 1

if __name__ == "__main__":
    exit(main())
