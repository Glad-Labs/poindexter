#!/usr/bin/env python3
"""
Test workflow execution end-to-end
"""

import requests
import json
import time
from datetime import datetime

BASE_URL = "http://localhost:8000"

# Configuration for each workflow
workflows = {
    "social_media": {
        "expected_phases": 5,
        "payload": {"topic": "AI trends in 2026", "platform": "linkedin", "tone": "professional"}
    },
    "email": {
        "expected_phases": 4,
        "payload": {"subject": "Weekly Newsletter", "target_audience": "subscribers"}
    },
    "blog_post": {
        "expected_phases": 7,
        "payload": {"topic": "The Future of AI Agents", "keywords": ["AI", "agents", "orchestration"], "audience": "tech professionals"}
    },
    "newsletter": {
        "expected_phases": 7,
        "payload": {"theme": "weekly tech digest", "sections": ["AI", "startups", "research"]}
    },
    "market_analysis": {
        "expected_phases": 5,
        "payload": {"sector": "artificial intelligence", "region": "global", "timeframe": "Q1 2026"}
    },
}

print("=" * 80)
print("WORKFLOW EXECUTION TEST SUITE")
print("=" * 80)
print(f"Test started at: {datetime.now().isoformat()}\n")

results = []

for template_name, config in workflows.items():
    print(f"\n--- Testing {template_name.upper()} ---")
    
    try:
        # Step 1: Execute workflow
        print(f"1. Executing {template_name} workflow...")
        execute_url = f"{BASE_URL}/api/workflows/execute/{template_name}"
        execute_response = requests.post(
            execute_url,
            json=config["payload"],
            timeout=5
        )
        
        if execute_response.status_code != 200:
            print(f"   ❌ Execution failed with status {execute_response.status_code}")
            print(f"   Response: {execute_response.text[:200]}")
            results.append({
                "template": template_name,
                "status": "FAILED",
                "reason": f"Execution returned {execute_response.status_code}"
            })
            continue
        
        # Parse response
        data = execute_response.json()
        workflow_id = data.get("workflow_id", "UNKNOWN")
        
        print(f"   ✅ Workflow created successfully")
        print(f"   Workflow ID: {workflow_id}")
        print(f"   Status: {data.get('status')}")
        print(f"   Phases: {len(data.get('phases', []))} (expected {config['expected_phases']})")
        
        # Step 2: Check workflow status
        print(f"\n2. Checking workflow status...")
        status_url = f"{BASE_URL}/api/workflows/status/{workflow_id}"
        status_response = requests.get(status_url, timeout=5)
        
        if status_response.status_code == 200:
            status_data = status_response.json()
            print(f"   ✅ Status endpoint working")
            print(f"   Status: {status_data.get('status', 'UNKNOWN')}")
            print(f"   Progress: {status_data.get('progress_percent', 0)}%")
        elif status_response.status_code == 404:
            print(f"   ⚠️  Status endpoint: 404 (workflow not yet tracked)")
        else:
            print(f"   ⚠️  Status endpoint returned {status_response.status_code}")
        
        # Validate response structure
        print(f"\n3. Validating response structure...")
        required_fields = ["workflow_id", "template", "status", "phases", "progress_percent", "started_at"]
        missing_fields = [f for f in required_fields if f not in data]
        
        if missing_fields:
            print(f"   ❌ Missing fields: {missing_fields}")
            results.append({
                "template": template_name,
                "status": "INCOMPLETE",
                "reason": f"Missing fields: {missing_fields}"
            })
        else:
            print(f"   ✅ All required fields present")
        
        # Validate phase count
        phase_count = len(data.get("phases", []))
        if phase_count != config["expected_phases"]:
            print(f"   ❌ Phase count mismatch: {phase_count} vs expected {config['expected_phases']}")
            results.append({
                "template": template_name,
                "status": "PHASE_MISMATCH",
                "reason": f"{phase_count} phases vs {config['expected_phases']} expected"
            })
        else:
            print(f"   ✅ Phase count matches: {phase_count}")
        
        # Success
        results.append({
            "template": template_name,
            "status": "SUCCESS",
            "workflow_id": workflow_id,
            "phase_count": phase_count,
            "expected_phases": config["expected_phases"]
        })
        
        print(f"\n✅ {template_name.upper()} test PASSED")
        
    except requests.exceptions.ConnectionError:
        print(f"   ❌ Cannot connect to {BASE_URL}")
        results.append({
            "template": template_name,
            "status": "CONNECTION_ERROR",
            "reason": f"Cannot reach {BASE_URL}"
        })
    except Exception as e:
        print(f"   ❌ Unexpected error: {type(e).__name__}: {e}")
        results.append({
            "template": template_name,
            "status": "ERROR",
            "reason": str(e)
        })

# Summary
print("\n" + "=" * 80)
print("TEST SUMMARY")
print("=" * 80)

passed = sum(1 for r in results if r["status"] == "SUCCESS")
failed = sum(1 for r in results if r["status"] != "SUCCESS")

print(f"\nResults: {passed} PASSED, {failed} FAILED\n")

for result in results:
    status_icon = "✅" if result["status"] == "SUCCESS" else "❌"
    print(f"{status_icon} {result['template']}: {result['status']}")
    if result["status"] != "SUCCESS":
        print(f"   Reason: {result.get('reason', 'Unknown')}")

print("\n" + "=" * 80)

# Test error case
print("\nBONUS: Testing invalid template error handling...")
try:
    response = requests.post(
        f"{BASE_URL}/api/workflows/execute/invalid_template",
        json={"test": "data"},
        timeout=5
    )
    
    if response.status_code == 404:
        print("✅ Invalid template properly returns 404")
        error_data = response.json()
        if "valid templates" in error_data.get("detail", "").lower():
            print("✅ Error message includes list of valid templates")
        else:
            print("⚠️  Error message missing valid templates list")
    else:
        print(f"❌ Invalid template returned {response.status_code} (expected 404)")

except Exception as e:
    print(f"❌ Error test failed: {e}")

print("\n" + "=" * 80)
print(f"Test completed at: {datetime.now().isoformat()}")
print("=" * 80)
