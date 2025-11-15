#!/usr/bin/env python3
"""
Insert test tasks into PostgreSQL database for Oversight Hub testing.
This populates the tasks table with sample data so you can see tasks in the UI.
"""

import psycopg2
import json
from uuid import uuid4
from datetime import datetime, timezone
import os

# Connection string from .env.local
DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/glad_labs_dev"

# Parse connection string
conn = psycopg2.connect(DATABASE_URL)
cursor = conn.cursor()

# Sample test tasks to insert
test_tasks = [
    {
        "task_name": "Generate Blog Post: AI Trends 2025",
        "topic": "AI Trends 2025",
        "primary_keyword": "artificial intelligence trends",
        "target_audience": "Tech professionals",
        "category": "Technology",
        "status": "completed",
        "agent_id": "content-agent"
    },
    {
        "task_name": "Generate Blog Post: Web Development Best Practices",
        "topic": "Web Development Best Practices",
        "primary_keyword": "web development practices",
        "target_audience": "Web developers",
        "category": "Development",
        "status": "in_progress",
        "agent_id": "content-agent"
    },
    {
        "task_name": "Generate Blog Post: Cloud Computing Guide",
        "topic": "Cloud Computing Guide",
        "primary_keyword": "cloud computing",
        "target_audience": "DevOps engineers",
        "category": "Infrastructure",
        "status": "pending",
        "agent_id": "content-agent"
    },
    {
        "task_name": "Generate Blog Post: Machine Learning Applications",
        "topic": "Machine Learning Applications",
        "primary_keyword": "machine learning",
        "target_audience": "Data scientists",
        "category": "AI",
        "status": "completed",
        "agent_id": "content-agent"
    },
    {
        "task_name": "Generate Blog Post: Security Best Practices",
        "topic": "Security Best Practices",
        "primary_keyword": "cybersecurity",
        "target_audience": "Security professionals",
        "category": "Security",
        "status": "pending",
        "agent_id": "content-agent"
    },
    {
        "task_name": "Generate Blog Post: API Design Patterns",
        "topic": "API Design Patterns",
        "primary_keyword": "api design",
        "target_audience": "Backend developers",
        "category": "Development",
        "status": "completed",
        "agent_id": "content-agent"
    },
]

# Insert tasks
inserted_count = 0
for task_data in test_tasks:
    try:
        task_id = str(uuid4())
        now = datetime.now(timezone.utc).isoformat()
        
        # Convert metadata to JSON string
        metadata = json.dumps({
            "created_by": "test_script",
            "source": "insert_test_tasks.py",
            "test_data": True
        })
        
        # Set completed_at for completed tasks
        completed_at = now if task_data["status"] == "completed" else None
        started_at = now if task_data["status"] in ["in_progress", "completed"] else None
        
        cursor.execute("""
            INSERT INTO tasks (
                id, task_name, agent_id, status, topic, primary_keyword,
                target_audience, category, created_at, updated_at,
                started_at, completed_at, task_metadata
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
        """, (
            task_id,
            task_data["task_name"],
            task_data["agent_id"],
            task_data["status"],
            task_data["topic"],
            task_data.get("primary_keyword", ""),
            task_data.get("target_audience", ""),
            task_data.get("category", "general"),
            now,  # created_at
            now,  # updated_at
            started_at,
            completed_at,
            metadata
        ))
        
        print(f"✅ Inserted task: {task_data['task_name']} (ID: {task_id})")
        inserted_count += 1
        
    except Exception as e:
        print(f"❌ Error inserting task {task_data['task_name']}: {e}")
        conn.rollback()
        raise

# Commit all inserts
conn.commit()
print(f"\n✅ Successfully inserted {inserted_count} test tasks")

# Verify insertion
cursor.execute("SELECT COUNT(*) FROM tasks;")
total_count = cursor.fetchone()[0]
print(f"📊 Total tasks in database: {total_count}")

# Show the tasks
cursor.execute("""
    SELECT id, task_name, status, created_at
    FROM tasks
    ORDER BY created_at DESC
    LIMIT 10
""")

print("\n📋 Recent tasks:")
for row in cursor.fetchall():
    print(f"  • {row[1]:<50} [{row[2]:>12}] - {row[3]}")

cursor.close()
conn.close()
print("\n✨ Done! Now refresh your browser to see the tasks in Oversight Hub")
