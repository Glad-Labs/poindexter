#!/usr/bin/env python3
"""
Debug Task Data Script
======================

Queries PostgreSQL directly to inspect task data and compare with API responses.
Helps debug:
- Why model_used is not displaying
- Why content is shorter than target_length

Usage:
    python scripts/debug-task-data.py <task_id>

Example:
    python scripts/debug-task-data.py 550e8400-e29b-41d4-a716-446655440000
"""

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import asyncpg
from dotenv import load_dotenv

# Load environment variables
load_dotenv(".env.local")


async def inspect_task(task_id: str):
    """Query database and inspect task data"""
    
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("❌ DATABASE_URL not found in .env.local")
        return
    
    conn = None
    try:
        # Connect to database
        conn = await asyncpg.connect(database_url)
        print(f"✅ Connected to database\n")
        
        # Query task
        query = """
            SELECT 
                id,
                task_name,
                task_type,
                status,
                topic,
                target_length,
                model_used,
                selected_model,
                models_used_by_phase,
                model_selection_log,
                task_metadata,
                result,
                quality_score,
                created_at,
                updated_at,
                completed_at
            FROM content_tasks
            WHERE id = $1
        """
        
        row = await conn.fetchrow(query, task_id)
        
        if not row:
            print(f"❌ Task {task_id} not found in database")
            return
        
        print("=" * 80)
        print(f"📋 TASK: {row['task_name'] or 'Untitled'}")
        print("=" * 80)
        print()
        
        # Basic info
        print("🔍 BASIC INFO:")
        print(f"  ID: {row['id']}")
        print(f"  Type: {row['task_type']}")
        print(f"  Status: {row['status']}")
        print(f"  Topic: {row['topic']}")
        print()
        
        # Content length analysis
        print("📏 CONTENT LENGTH:")
        print(f"  Target Length: {row['target_length']} words")
        
        # Parse result to get actual content
        if row['result']:
            result = json.loads(row['result']) if isinstance(row['result'], str) else row['result']
            if 'content' in result:
                content = result['content']
                actual_words = len(content.split())
                target = row['target_length'] or 0
                
                percentage = (actual_words / target * 100) if target else 0
                status_emoji = "✅" if 90 <= percentage <= 110 else "⚠️"
                
                print(f"  Actual Length: {actual_words} words {status_emoji}")
                if target:
                    print(f"  Percentage: {percentage:.1f}%")
                    print(f"  Difference: {actual_words - target:+d} words")
            else:
                print("  ⚠️  No content in result")
        else:
            print("  ⚠️  No result data")
        print()
        
        # Model information
        print("🤖 MODEL INFORMATION:")
        print(f"  model_used: {row['model_used'] or 'NULL ❌'}")
        print(f"  selected_model: {row['selected_model'] or 'NULL'}")
        
        if row['models_used_by_phase']:
            models_by_phase = json.loads(row['models_used_by_phase']) if isinstance(row['models_used_by_phase'], str) else row['models_used_by_phase']
            print(f"  models_used_by_phase:")
            for phase, model in models_by_phase.items():
                print(f"    - {phase}: {model}")
        else:
            print(f"  models_used_by_phase: NULL")
        
        if row['model_selection_log']:
            selection_log = json.loads(row['model_selection_log']) if isinstance(row['model_selection_log'], str) else row['model_selection_log']
            print(f"  model_selection_log: {json.dumps(selection_log, indent=4)}")
        else:
            print(f"  model_selection_log: NULL")
        print()
        
        # Metadata inspection
        print("📦 METADATA:")
        if row['task_metadata']:
            metadata = json.loads(row['task_metadata']) if isinstance(row['task_metadata'], str) else row['task_metadata']
            print(f"  task_metadata keys: {list(metadata.keys())}")
            if 'target_length' in metadata:
                print(f"    - target_length: {metadata['target_length']}")
            if 'model_used' in metadata:
                print(f"    - model_used: {metadata['model_used']}")
        else:
            print("  task_metadata: NULL")
        print()
        
        # Result inspection
        print("📊 RESULT DATA:")
        if row['result']:
            result = json.loads(row['result']) if isinstance(row['result'], str) else row['result']
            print(f"  result keys: {list(result.keys())}")
            print(f"  Quality Score: {row['quality_score'] or 'Not rated'}")
            if 'quality_score' in result:
                print(f"  Result quality_score: {result['quality_score']}")
        else:
            print("  result: NULL")
        print()
        
        # Timestamps
        print("⏱️  TIMESTAMPS:")
        print(f"  Created: {row['created_at']}")
        print(f"  Updated: {row['updated_at']}")
        print(f"  Completed: {row['completed_at'] or 'Not completed'}")
        
        if row['completed_at'] and row['created_at']:
            duration = row['completed_at'] - row['created_at']
            print(f"  Duration: {duration}")
        print()
        
        # Summary
        print("=" * 80)
        print("🔍 DIAGNOSIS:")
        print("=" * 80)
        issues = []
        
        if not row['model_used']:
            issues.append("❌ model_used is NULL - not being stored during generation")
        
        if row['target_length']:
            if row['result']:
                result = json.loads(row['result']) if isinstance(row['result'], str) else row['result']
                if 'content' in result:
                    actual = len(result['content'].split())
                    target = row['target_length']
                    percentage = (actual / target * 100)
                    if percentage < 90 or percentage > 110:
                        issues.append(f"⚠️  Content length {percentage:.1f}% of target (expected 90-110%)")
        
        if not issues:
            issues.append("✅ No obvious data issues detected")
        
        for issue in issues:
            print(f"  {issue}")
        print()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if conn:
            await conn.close()
            print("✅ Database connection closed")


def main():
    if len(sys.argv) != 2:
        print("Usage: python scripts/debug-task-data.py <task_id>")
        print("\nExample:")
        print("  python scripts/debug-task-data.py 550e8400-e29b-41d4-a716-446655440000")
        sys.exit(1)
    
    task_id = sys.argv[1]
    asyncio.run(inspect_task(task_id))


if __name__ == "__main__":
    main()
