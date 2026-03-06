#!/usr/bin/env python3
"""
Test API Response Script
========================

Tests the /api/tasks/{task_id} endpoint and compares with database data.

Usage:
    python scripts/test-api-response.py <task_id>

Example:
    python scripts/test-api-response.py 550e8400-e29b-41d4-a716-446655440000
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Load environment variables
load_dotenv(".env.local")


async def test_api_endpoint(task_id: str):
    """Test API endpoint and compare with expected data"""
    
    api_url = os.getenv("VITE_API_URL", "http://localhost:8000")
    
    print(f"🌐 Testing API: {api_url}/api/tasks/{task_id}")
    print()
    
    try:
        async with httpx.AsyncClient() as client:
            # Test with dev token
            headers = {
                "Authorization": "Bearer dev-token",
                "Content-Type": "application/json"
            }
            
            response = await client.get(
                f"{api_url}/api/tasks/{task_id}",
                headers=headers,
                timeout=30.0
            )
            
            print(f"📡 Response Status: {response.status_code}")
            print()
            
            if response.status_code != 200:
                print(f"❌ API Error: {response.text}")
                return
            
            data = response.json()
            
            # Analyze response
            print("=" * 80)
            print("📦 API RESPONSE DATA")
            print("=" * 80)
            print()
            
            print("🔍 KEY FIELDS:")
            key_fields = [
                'id', 'task_name', 'task_type', 'status', 'topic',
                'target_length', 'model_used', 'selected_model',
                'quality_score', 'created_at', 'updated_at', 'completed_at'
            ]
            
            for field in key_fields:
                value = data.get(field)
                if value is not None:
                    print(f"  ✓ {field}: {value}")
                else:
                    print(f"  ✗ {field}: NOT PRESENT ❌")
            print()
            
            print("🤖 MODEL TRACKING:")
            print(f"  model_used: {data.get('model_used', 'NOT PRESENT ❌')}")
            print(f"  selected_model: {data.get('selected_model', 'NOT PRESENT')}")
            
            if 'models_by_phase' in data:
                print(f"  models_by_phase: {data['models_by_phase']}")
            else:
                print(f"  models_by_phase: NOT PRESENT")
            
            if 'model_selection_log' in data:
                print(f"  model_selection_log: Present")
            else:
                print(f"  model_selection_log: NOT PRESENT")
            print()
            
            print("📏 CONTENT LENGTH:")
            print(f"  target_length: {data.get('target_length', 'NOT PRESENT')}")
            
            # Check content in result
            if 'result' in data:
                result = data['result']
                if isinstance(result, dict) and 'content' in result:
                    actual_words = len(result['content'].split())
                    print(f"  Actual words in content: {actual_words}")
                    
                    target = data.get('target_length')
                    if target:
                        percentage = (actual_words / target * 100)
                        status = "✅" if 90 <= percentage <= 110 else "⚠️"
                        print(f"  Percentage of target: {percentage:.1f}% {status}")
                else:
                    print(f"  ⚠️  result exists but no content field")
            elif 'content' in data:
                actual_words = len(data['content'].split())
                print(f"  Actual words in content: {actual_words}")
            else:
                print(f"  ⚠️  No content found in response")
            print()
            
            print("📦 ALL RESPONSE KEYS:")
            print(f"  {', '.join(sorted(data.keys()))}")
            print()
            
            # Check nested objects
            if 'task_metadata' in data:
                print("📦 TASK_METADATA:")
                metadata = data['task_metadata']
                if isinstance(metadata, dict):
                    print(f"  Keys: {', '.join(metadata.keys())}")
                    if 'model_used' in metadata:
                        print(f"  Contains model_used: {metadata['model_used']}")
                    if 'target_length' in metadata:
                        print(f"  Contains target_length: {metadata['target_length']}")
                else:
                    print(f"  Type: {type(metadata)}")
                print()
            
            if 'result' in data:
                print("📦 RESULT:")
                result = data['result']
                if isinstance(result, dict):
                    print(f"  Keys: {', '.join(result.keys())}")
                else:
                    print(f"  Type: {type(result)}")
                print()
            
            # Save full response
            output_file = Path(__file__).parent / f"api_response_{task_id[:8]}.json"
            with open(output_file, 'w') as f:
                json.dump(data, f, indent=2, default=str)
            print(f"💾 Full response saved to: {output_file}")
            print()
            
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/test-api-response.py <task_id>")
        print("\nExample:")
        print("  python scripts/test-api-response.py 550e8400-e29b-41d4-a716-446655440000")
        sys.exit(1)
    
    task_id = sys.argv[1]
    asyncio.run(test_api_endpoint(task_id))


if __name__ == "__main__":
    main()
