#!/usr/bin/env python3
"""Test workflow API response with detailed diagnostics"""

import asyncio
import aiohttp
import time
from datetime import datetime

async def test_endpoint():
    """Test the available-phases endpoint"""
    url = "http://localhost:8000/api/workflows/available-phases"
    
    print(f"[{datetime.now().isoformat()}] Testing endpoint: {url}")
    print("-" * 80)
    
    try:
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            start = time.time()
            
            print(f"[{datetime.now().isoformat()}] Sending request...")
            async with session.get(url) as resp:
                elapsed = time.time() - start
                
                print(f"[{datetime.now().isoformat()}] ✓ Response received in {elapsed:.2f}s")
                print(f"Status Code: {resp.status}")
                print(f"Content-Type: {resp.headers.get('content-type')}")
                
                if resp.status == 200:
                    data = await resp.json()
                    phases = data.get("phases", [])
                    print(f"✓ SUCCESS: Response contains {len(phases)} phases")
                    print(f"Phase names: {[p['name'] for p in phases]}")
                else:
                    print(f"✗ Error response:")
                    print(await resp.text())
                    
    except asyncio.TimeoutError as e:
        print(f"✗ TIMEOUT ERROR: {e}")
        print(f"  Request exceeded timeout limit")
    except aiohttp.ClientError as e:
        print(f"✗ CLIENT ERROR: {e}")
    except Exception as e:
        print(f"✗ UNEXPECTED ERROR: {type(e).__name__}: {e}")
    finally:
        print("-" * 80)
        print(f"[{datetime.now().isoformat()}] Test completed")

if __name__ == "__main__":
    asyncio.run(test_endpoint())
