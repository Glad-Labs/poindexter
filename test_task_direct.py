#!/usr/bin/env python
"""Direct test of task creation"""

import asyncio
import asyncpg
from src.cofounder_agent.services.database_service import DatabaseService

async def test_task_creation():
    # Create database service
    db = DatabaseService()
    await db.initialize()
    
    # Try to create a task
    task_data = {
        "task_name": "Test Task",
        "topic": "AI Trends",
        "primary_keyword": "AI",
        "target_audience": "Developers",
        "category": "tech",
        "metadata": {},
    }
    
    try:
        task_id = await db.add_task(task_data)
        print(f"✅ Task created: {task_id}")
        
        # Retrieve it
        task = await db.get_task(task_id)
        print(f"✅ Task retrieved: {task}")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        await db.close()

if __name__ == "__main__":
    asyncio.run(test_task_creation())
