#!/usr/bin/env python
"""Quick API endpoint verification"""
import json
import sys

import requests

BASE_URL = "http://localhost:8000"

print("=" * 60)
print("  WORKFLOW API ENDPOINT VERIFICATION")
print("=" * 60)
print()

# Test 1: Health check
print("Test 1: Health Check")
try:
    resp = requests.get(f"{BASE_URL}/health", timeout=5)
    print(f"  [OK] Backend is running (status: {resp.status_code})")
except Exception as e:
    print(f"  [ERROR] Backend not accessible: {e}")
    sys.exit(1)
print()

# Test 2: Get available phases
print("Test 2: Get Available Phases")
try:
    resp = requests.get(f"{BASE_URL}/api/workflows/available-phases", timeout=5)
    if resp.status_code == 200:
        data = resp.json()
        phases = data.get("phases", [])
        print(f"  [OK] Found {len(phases)} phases")
        for phase in phases[:3]:
            print(f"    - {phase.get('name')}")
        if len(phases) > 3:
            print(f"    ... and {len(phases) - 3} more")
    else:
        print(f"  [ERROR] Status {resp.status_code}: {resp.text[:200]}")
except Exception as e:
    print(f"  [ERROR] Request failed: {e}")
print()

print("=" * 60)
print("  VERIFICATION COMPLETE")
print("=" * 60)
