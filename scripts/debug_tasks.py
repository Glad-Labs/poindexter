#!/usr/bin/env python3
"""Debug script to inspect tasks in database"""
import asyncio
import json
import psycopg
from datetime import datetime

async def debug_tasks():
    """Query tasks and show their content"""
    conn_string = "postgresql://postgres:postgres@localhost:5432/glad_labs_dev"
    
    try:
        async with await psycopg.AsyncConnection.connect(conn_string) as conn:
            # Get all tasks
            query = "SELECT id, task_name, topic, status, metadata, result FROM tasks ORDER BY created_at DESC LIMIT 10"
            
            print("=" * 100)
            print("RECENT TASKS IN DATABASE")
            print("=" * 100)
            
            async with conn.cursor() as cur:
                await cur.execute(query)
                tasks = await cur.fetchall()
                
                for i, task_row in enumerate(tasks, 1):
                    task_id, task_name, topic, status, metadata, result = task_row
                    
                    print(f"\n[Task {i}] ID: {task_id}")
                    print(f"  Name: {task_name}")
                    print(f"  Topic: {topic}")
                    print(f"  Status: {status}")
                    
                    # Parse and display metadata
                    if metadata:
                        if isinstance(metadata, str):
                            meta = json.loads(metadata)
                        else:
                            meta = metadata
                        
                        if 'content' in meta:
                            content_preview = meta['content'][:200] if isinstance(meta['content'], str) else str(meta['content'])[:200]
                            print(f"  Metadata Content Preview: {content_preview}...")
                            if 'generated_at' in meta:
                                print(f"  Generated At: {meta['generated_at']}")
                        else:
                            print(f"  Metadata: {list(meta.keys())}")
                    
                    # Parse and display result
                    if result:
                        if isinstance(result, str):
                            res = json.loads(result)
                        else:
                            res = result
                        print(f"  Result: {json.dumps(res, indent=4)[:300]}")
                    
            print("\n" + "=" * 100)
            print("TASK STATISTICS")
            print("=" * 100)
            
            # Get statistics
            stat_query = """
                SELECT 
                    status,
                    COUNT(*) as count,
                    COUNT(CASE WHEN metadata->>'content' IS NOT NULL THEN 1 END) as with_content
                FROM tasks
                GROUP BY status
            """
            
            async with conn.cursor() as cur:
                await cur.execute(stat_query)
                stats = await cur.fetchall()
                
                for status, count, with_content in stats:
                    print(f"Status: {status:20} | Total: {count:3} | With Content: {with_content:3}")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(debug_tasks())
