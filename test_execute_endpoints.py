#!/usr/bin/env python3
"""
Test all workflow execution endpoints to verify they work correctly.
"""

import requests
import json
import sys

BASE_URL = "http://localhost:8000"

templates = {
    "social_media": {
        "expected_phases": 5,
        "data": {"topic": "AI trends", "platform": "linkedin"}
    },
    "email": {
        "expected_phases": 4,
        "data": {"subject": "Newsletter", "recipient": "users@company.com"}
    },
    "blog_post": {
        "expected_phases": 7,
        "data": {"topic": "Future of AI", "keywords": ["AI", "future"]}
    },
    "newsletter": {
        "expected_phases": 7,
        "data": {"theme": "weekly", "audience": "subscribers"}
    },
    "market_analysis": {
        "expected_phases": 5,
        "data": {"sector": "technology", "region": "global"}
    },
}

print("=" * 70)
print("WORKFLOW EXECUTION ENDPOINT TESTS")
print("=" * 70)

passed = 0
failed = 0

for template_name, test_config in templates.items():
    url = f"{BASE_URL}/api/workflows/execute/{template_name}"
    expected_phases = test_config["expected_phases"]
    payload = test_config["data"]
    
    try:
        response = requests.post(url, json=payload, timeout=5)
        
        # Check status code
        if response.status_code != 200:
            print(f"\n❌ {template_name.upper()}")
            print(f"   Status: {response.status_code} (expected 200)")
            print(f"   Response: {response.text[:200]}")
            failed += 1
            continue
        
        # Parse response
        data = response.json()
        
        # Validate response structure
        required_fields = ["workflow_id", "template", "status", "phases", "progress_percent", "started_at"]
        missing_fields = [f for f in required_fields if f not in data]
        
        if missing_fields:
            print(f"\n❌ {template_name.upper()}")
            print(f"   Missing fields: {missing_fields}")
            failed += 1
            continue
        
        # Validate phase count
        phase_count = len(data["phases"])
        if phase_count != expected_phases:
            print(f"\n⚠️  {template_name.upper()}")
            print(f"   Phase count: {phase_count} (expected {expected_phases})")
            print(f"   Phases: {data['phases']}")
            failed += 1
            continue
        
        # Success
        print(f"\n✅ {template_name.upper()}")
        print(f"   Workflow ID: {data['workflow_id'][:8]}...")
        print(f"   Status: {data['status']}")
        print(f"   Phases: {phase_count}")
        print(f"   Quality Threshold: {data.get('quality_threshold', 'N/A')}")
        print(f"   Started: {data['started_at']}")
        passed += 1
        
    except requests.exceptions.ConnectionError:
        print(f"\n❌ {template_name.upper()}")
        print(f"   ERROR: Cannot connect to {BASE_URL}")
        failed += 1
    except Exception as e:
        print(f"\n❌ {template_name.upper()}")
        print(f"   ERROR: {type(e).__name__}: {e}")
        failed += 1

print("\n" + "=" * 70)
print(f"RESULTS: {passed} passed, {failed} failed")
print("=" * 70)

# Also test error cases
print("\n" + "=" * 70)
print("ERROR HANDLING TESTS")
print("=" * 70)

# Test invalid template
try:
    response = requests.post(f"{BASE_URL}/api/workflows/execute/invalid_template", json={"test": "data"}, timeout=5)
    if response.status_code == 404:
        print("\n✅ Invalid template returns 404")
    else:
        print(f"\n❌ Invalid template returns {response.status_code} (expected 404)")
except Exception as e:
    print(f"\n❌ Invalid template test failed: {e}")

# Test missing params
try:
    response = requests.post(f"{BASE_URL}/api/workflows/execute/social_media", json={}, timeout=5)
    if response.status_code == 200:
        print("✅ Empty params accepted (workflow creation still succeeds)")
    else:
        print(f"ℹ️  Empty params returns {response.status_code}")
except Exception as e:
    print(f"❌ Empty params test failed: {e}")

print("\n" + "=" * 70)

sys.exit(0 if failed == 0 else 1)
