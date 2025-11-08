#!/usr/bin/env python3
"""Verify PostgreSQL database has content_tasks table with recent tasks"""

import psycopg2
from datetime import datetime

try:
    conn = psycopg2.connect(
        'postgresql://postgres:postgres@localhost:5432/glad_labs_dev'
    )
    cursor = conn.cursor()
    
    # Check tables
    cursor.execute("""
        SELECT table_name FROM information_schema.tables 
        WHERE table_schema = 'public'
    """)
    tables = [row[0] for row in cursor.fetchall()]
    print(f"📊 Tables in glad_labs_dev database:")
    for table in sorted(tables):
        print(f"  ✅ {table}")
    print()
    
    # Check content_tasks table
    if 'content_tasks' in tables:
        cursor.execute("SELECT COUNT(*) FROM content_tasks")
        count = cursor.fetchone()[0]
        print(f"📝 Total tasks in content_tasks table: {count}")
        print()
        
        # Show recent tasks
        cursor.execute("""
            SELECT task_id, status, topic, created_at 
            FROM content_tasks 
            ORDER BY created_at DESC 
            LIMIT 5
        """)
        rows = cursor.fetchall()
        
        if rows:
            print("📋 Recent tasks (last 5):")
            for i, (task_id, status, topic, created_at) in enumerate(rows, 1):
                topic_short = (topic[:50] + "...") if len(topic) > 50 else topic
                print(f"  {i}. {task_id}")
                print(f"     Status: {status}")
                print(f"     Topic:  {topic_short}")
                print(f"     Date:   {created_at}")
        else:
            print("   (No tasks in database)")
    else:
        print("❌ content_tasks table NOT found!")
    
    conn.close()
    print()
    print("✅ PostgreSQL verification complete")
    
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
