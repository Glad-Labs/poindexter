#!/usr/bin/env python3
"""Quick test of workflow execution endpoints"""

import requests
import json

BASE_URL = "http://localhost:8000"

def test_endpoint(template_name, expected_phases):
    """Test a single workflow template"""
    url = f"{BASE_URL}/api/workflows/execute/{template_name}"
    payload = {
        "topic": f"Test {template_name}",
        "input": "test data"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=5)
        
        if response.status_code != 200:
            return f"FAIL: {template_name} returned {response.status_code}"
        
        data = response.json()
        
        # Check key fields
        if "workflow_id" not in data:
            return f"FAIL: {template_name} missing workflow_id"
        
        if len(data.get("phases", [])) != expected_phases:
            return f"FAIL: {template_name} has {len(data.get('phases', []))} phases, expected {expected_phases}"
        
        return f"PASS: {template_name}"
    
    except Exception as e:
        return f"ERROR: {template_name} - {e}"

# Test all templates
templates = {
    "social_media": 5,
    "email": 4,
    "blog_post": 7,
    "newsletter": 7,
    "market_analysis": 5,
}

print("Testing Workflow Execution Endpoints")
print("=" * 40)

for template, expected_phases in templates.items():
    print(test_endpoint(template, expected_phases))

print("=" * 40)
