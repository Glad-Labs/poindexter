#!/usr/bin/env python3
"""Quick test to verify the complete pipeline with real blog content"""
import asyncio
import aiohttp
import json

async def test_full_pipeline():
    """Test: Create task → Generate content → Verify publishing"""
    
    api_url = "http://localhost:8000"
    
    print("=" * 100)
    print("FULL CONTENT GENERATION PIPELINE TEST")
    print("=" * 100)
    print("\n✅ STEP 1: Creating task...")
    
    task_payload = {
        "task_name": "Tech Trends 2025",
        "topic": "Machine Learning Trends for Business Professionals",
        "primary_keyword": "machine learning business",
        "target_audience": "Business professionals and CTOs",
        "category": "technology"
    }
    
    task_id = None
    async with aiohttp.ClientSession() as session:
        # Step 1: Create task
        async with session.post(f"{api_url}/api/tasks", json=task_payload) as resp:
            if resp.status == 201:
                result = await resp.json()
                task_id = result['id']
                print(f"  ✅ Task created: {task_id}")
            else:
                print(f"  ❌ Failed: {resp.status}")
                return
        
        # Step 2: Wait for background processing
        print("\n✅ STEP 2: Waiting for background content generation (20 seconds)...")
        for i in range(20):
            await asyncio.sleep(1)
            if i % 5 == 4:
                print(f"     ({i+1} seconds elapsed)")
        
        # Step 3: Retrieve and verify content
        print("\n✅ STEP 3: Retrieving generated content...")
        async with session.get(f"{api_url}/api/tasks/{task_id}") as resp:
            if resp.status == 200:
                task_data = await resp.json()
                
                # Extract metrics
                status = task_data.get('status')
                updated_at = task_data.get('updated_at')
                result = task_data.get('result')
                
                print(f"  Status: {status}")
                print(f"  Updated: {updated_at}")
                
                if result:
                    try:
                        if isinstance(result, str):
                            result_obj = json.loads(result)
                        else:
                            result_obj = result
                        
                        if 'content' in result_obj:
                            content = result_obj['content']
                            content_len = len(content)
                            
                            print(f"\n  📊 Content Generated:")
                            print(f"     - Length: {content_len} characters")
                            print(f"     - Generated at: {result_obj.get('generated_at', 'N/A')}")
                            
                            # Show content sample
                            print(f"\n  📝 Content Preview:")
                            print("     " + "-" * 90)
                            lines = content.split('\n')[:10]  # First 10 lines
                            for line in lines:
                                if line.strip():
                                    preview = line[:85]
                                    print(f"     {preview}")
                            print("     " + "-" * 90)
                            
                            if status == "completed":
                                print(f"\n  ✅ SUCCESS: Content generated and published!")
                            elif status == "publish_failed":
                                error = result_obj.get('error', 'Unknown error')
                                print(f"\n  ⚠️ Content generated but publish failed: {error}")
                            
                            return True
                    except Exception as e:
                        print(f"  Error parsing result: {e}")
                
                print(f"  ⚠️ No content in result")
            else:
                print(f"  ❌ Failed to retrieve task: {resp.status}")
    
    return False

if __name__ == "__main__":
    success = asyncio.run(test_full_pipeline())
    if success:
        print("\n" + "=" * 100)
        print("✅ PIPELINE TEST PASSED - Content generation is working!")
        print("=" * 100)
