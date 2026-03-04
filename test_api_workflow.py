#!/usr/bin/env python3
"""
Direct API test of blog workflow execution with model parameter.
This bypasses the UI and tests the backend directly via HTTP.
"""
import requests
import json
import time
import sys
from datetime import datetime

BASE_URL = "http://localhost:8000"
HEADERS = {
    "Authorization": "Bearer dev-token-123",
    "Content-Type": "application/json"
}

def test_blog_workflow():
    print("=" * 80)
    print("DIRECT API TEST: Blog Workflow with Model Parameter")
    print("=" * 80)
    
    # Get available workflows
    print("\n[1] Checking available workflow templates...")
    try:
        resp = requests.get(f"{BASE_URL}/api/workflow/templates", headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            templates = resp.json()
            print("[OK] Available templates: {} found".format(len(templates) if isinstance(templates, list) else 'list'))
            for template in (templates if isinstance(templates, list) else []):
                print("  - {}".format(template.get('name', template) if isinstance(template, dict) else template))
        else:
            print("[FAIL] Status: {}".format(resp.status_code))
            print("  Response: {}".format(resp.text[:200]))
    except Exception as e:
        print("[ERROR] {}".format(e))
    
    # Test 1: Execute blog_post workflow with content agent
    print("\n[2] Executing blog_post workflow with model selection...")
    # Blog Post workflow phases: research -> draft -> assess -> refine -> image -> publish
    # Research phase needs: topic (required), focus (optional)
    # Draft phase needs: prompt (required), content, target_audience, tone (optional)
    # We'll provide inputs progressively - research first, then draft will auto-map research.findings to draft.content
    payload = {
        "topic": "The Benefits of Artificial Intelligence in Healthcare",
        "focus": "medical diagnosis, drug discovery, personalized medicine, ethical considerations",
        # For the draft phase, we can provide a prompt or let it auto-map from research findings
        "prompt": "Create an engaging blog post based on the research findings about AI in healthcare. Make it suitable for healthcare professionals and IT decision-makers.",
        "target_audience": "healthcare professionals and IT decision-makers",
        "tone": "professional",
        "model": "ollama-mistral"  # Include model in task_input
    }
    
    try:
        resp = requests.post(
            f"{BASE_URL}/api/workflows/execute/blog_post",
            json=payload,
            headers=HEADERS,
            timeout=30
        )
        
        result = resp.json()
        print("Status: {}".format(resp.status_code))
        
        if "execution_id" in result:
            execution_id = result["execution_id"]
            workflow_id = result.get("workflow_id")
            status = result.get("status")
            error = result.get("error_message")
            
            print("[OK] Workflow execution initiated")
            print("  Execution ID: {}".format(execution_id))
            print("  Workflow ID: {}".format(workflow_id))
            print("  Status: {}".format(status))
            
            if error:
                print("  Error: {}".format(error))
                
            return execution_id
        else:
            print("[FAIL] Invalid response: {}".format(result))
            return None
            
    except Exception as e:
        print("[ERROR] {}".format(e))
        return None

def check_database_for_model(execution_id):
    """Check if the model was saved to database"""
    if not execution_id:
        return
        
    print(f"\n[3] Checking database for execution {execution_id[:8]}...")
    
    try:
        import asyncio
        from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
        from sqlalchemy.orm import sessionmaker
        from sqlalchemy import text
        
        async def check():
            db_url = 'postgresql://postgres:postgres@localhost:5432/glad_labs_dev'
            engine = create_async_engine(
                db_url.replace('postgresql://', 'postgresql+asyncpg://'),
                echo=False
            )
            AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession)
            
            async with AsyncSessionLocal() as session:
                result = await session.execute(text("""
                    SELECT 
                        id, 
                        selected_model, 
                        execution_status,
                        initial_input,
                        created_at
                    FROM workflow_executions 
                    WHERE id = :exec_id
                    LIMIT 1
                """), {"exec_id": execution_id})
                
                row = result.fetchone()
                
                if row:
                    wf_id, model, status, initial_input, created = row
                    print("[OK] Found execution in database")
                    print("  Selected Model: {}".format(model if model else '(NULL - not captured)'))
                    print("  Status: {}".format(status))
                    print("  Created: {}".format(created))
                    
                    if initial_input and isinstance(initial_input, dict):
                        print("  Input Data: {}".format(json.dumps(initial_input, indent=2)[:200]))
                else:
                    print("[FAIL] Execution not found in database (may not exist yet)")
            
            await engine.dispose()
        
        asyncio.run(check())
        
    except Exception as e:
        print("[ERROR] Database check error: {}".format(e))

if __name__ == "__main__":
    exec_id = test_blog_workflow()
    
    # Give backend time to process
    if exec_id:
        print("\n(Waiting 2 seconds for backend to process...)")
        time.sleep(2)
        check_database_for_model(exec_id)
    
    print("\n" + "=" * 80)
    print("NEXT: Check if 'selected_model' column is populated with actual value")
    print("=" * 80)
