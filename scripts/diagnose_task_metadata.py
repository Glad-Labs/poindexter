"""
Diagnostic script to check task metadata in database
Investigates why task metadata doesn't match the intent when posts are created
"""
import asyncio
import os
import sys
from pathlib import Path

# Add src to path so we can import cofounder_agent modules
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import asyncpg
from dotenv import load_dotenv

# Load environment
load_dotenv(Path(__file__).parent.parent / ".env.local")


async def diagnose_tasks():
    """Check recent tasks for metadata issues"""
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        print("❌ DATABASE_URL not found in environment")
        return

    try:
        conn = await asyncpg.connect(database_url)
        print("✅ Connected to database\n")

        # Get the 5 most recent tasks
        query = """
            SELECT 
                task_id,
                topic,
                style,
                tone,
                target_length,
                model_selections,
                models_used_by_phase,
                quality_preference,
                model_used,
                created_at,
                status
            FROM content_tasks
            ORDER BY created_at DESC
            LIMIT 10
        """

        rows = await conn.fetch(query)

        print(f"📊 Found {len(rows)} recent tasks\n")
        print("=" * 100)

        for i, row in enumerate(rows, 1):
            print(f"\nTask #{i}:")
            print(f"  ID: {row['task_id']}")
            print(f"  Topic: {row['topic']}")
            print(f"  Status: {row['status']}")
            print(f"  Created: {row['created_at']}")
            print(f"  ---")
            print(f"  Style: '{row['style']}' (type: {type(row['style']).__name__})")
            print(f"  Tone: '{row['tone']}' (type: {type(row['tone']).__name__})")
            print(f"  Target Length: {row['target_length']}")
            print(f"  Quality Pref: '{row['quality_preference']}'")
            print(f"  ---")
            print(f"  Model Used: {row['model_used']}")
            print(f"  Model Selections (JSONB): {row['model_selections']}")
            print(f"  Models Used By Phase (JSONB): {row['models_used_by_phase']}")

            # Check for mismatches
            issues = []
            if row['style'] == 'technical' and row['topic']:
                issues.append("⚠️  Style is 'technical' (database default) - may not match user intent")
            if row['style'] is None or row['style'] == '':
                issues.append("❌ Style is NULL or empty!")
            if row['tone'] is None or row['tone'] == '':
                issues.append("❌ Tone is NULL or empty!")
            if row['model_selections'] == {} or row['model_selections'] is None:
                issues.append("⚠️  No model selections specified")
            if row['model_used'] is None and row['status'] not in ['pending', 'failed']:
                issues.append("⚠️  Task completed but no model_used recorded")

            if issues:
                print(f"\n  🔍 ISSUES DETECTED:")
                for issue in issues:
                    print(f"     {issue}")

            print("-" * 100)

        await conn.close()
        print("\n✅ Diagnosis complete")

    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(diagnose_tasks())
