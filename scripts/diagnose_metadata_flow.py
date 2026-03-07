#!/usr/bin/env python3
"""
Diagnose Task Metadata Flow
Traces metadata from UI → Backend → Database

This script helps diagnose why task metadata (style, tone, model selections)
isn't matching user intent when posts are created.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root / "src" / "cofounder_agent"))

import asyncpg


async def diagnose_metadata_flow():
    """Analyze task metadata flow from creation to storage."""
    
    # Connect to database
    database_url = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/glad_labs_dev")
    
    try:
        conn = await asyncpg.connect(database_url)
        print("✅ Connected to database")
        print("=" * 80)
        
        # Get recent tasks
        query = """
            SELECT 
                task_id,
                task_type,
                topic,
                style,
                tone,
                model_selections,
                quality_preference,
                task_metadata,
                created_at
            FROM content_tasks
            ORDER BY created_at DESC
            LIMIT 10
        """
        
        rows = await conn.fetch(query)
        
        print(f"\n📊 Analysis of {len(rows)} Most Recent Tasks\n")
        print("=" * 80)
        
        # Track issues
        style_issues = []
        tone_issues = []
        model_selection_issues = []
        
        for idx, row in enumerate(rows, 1):
            task_id = row['task_id']
            print(f"\n🔍 Task #{idx}: {task_id}")
            print(f"   Type: {row['task_type']}")
            print(f"   Topic: {row['topic'][:50]}...")
            print(f"   Created: {row['created_at']}")
            
            # Check style
            style = row['style']
            print(f"   📝 Style: {style}", end="")
            if style == 'technical':
                print(" ⚠️  DEFAULT (user may not have selected this)")
                style_issues.append((task_id, style))
            else:
                print(" ✅")
            
            # Check tone
            tone = row['tone']
            print(f"   🎭 Tone: {tone}", end="")
            if tone == 'professional':
                print(" ⚠️  DEFAULT (check if this was user's choice)")
                tone_issues.append((task_id, tone))
            else:
                print(" ✅")
            
            # Check model selections
            model_selections = row['model_selections']
            try:
                if isinstance(model_selections, str):
                    models = json.loads(model_selections) if model_selections else {}
                else:
                    models = model_selections or {}
                
                print(f"   🤖 Model Selections: ", end="")
                if not models or models == {}:
                    print("❌ EMPTY (user selections not saved)")
                    model_selection_issues.append((task_id, 'empty'))
                else:
                    print(f"✅ {len(models)} phases configured")
                    for phase, model in models.items():
                        print(f"      - {phase}: {model}")
            except json.JSONDecodeError:
                print(f"   ⚠️  Invalid JSON: {model_selections}")
                model_selection_issues.append((task_id, 'invalid_json'))
            
            # Check quality preference
            quality_pref = row['quality_preference']
            print(f"   ⚡ Quality: {quality_pref}")
            
            # Check task_metadata for additional info
            try:
                metadata = row['task_metadata']
                if isinstance(metadata, str):
                    metadata = json.loads(metadata) if metadata else {}
                else:
                    metadata = metadata or {}
                
                # Check if metadata has different values than top-level fields
                metadata_style = metadata.get('style')
                metadata_tone = metadata.get('tone')
                
                if metadata_style and metadata_style != style:
                    print(f"   ⚠️  MISMATCH: metadata.style='{metadata_style}' != DB style='{style}'")
                
                if metadata_tone and metadata_tone != tone:
                    print(f"   ⚠️  MISMATCH: metadata.tone='{metadata_tone}' != DB tone='{tone}'")
                    
            except (json.JSONDecodeError, TypeError) as e:
                print(f"   ⚠️  Could not parse metadata: {e}")
        
        # Summary
        print("\n" + "=" * 80)
        print("\n📈 SUMMARY OF ISSUES\n")
        print("=" * 80)
        
        print(f"\n🎨 Style Issues: {len(style_issues)}/{len(rows)}")
        if style_issues:
            print("   Tasks with default 'technical' style:")
            for task_id, style in style_issues:
                print(f"   - {task_id}: {style}")
        
        print(f"\n🎭 Tone Issues: {len(tone_issues)}/{len(rows)}")
        if tone_issues:
            print("   Tasks with default 'professional' tone:")
            for task_id, tone in tone_issues:
                print(f"   - {task_id}: {tone}")
        
        print(f"\n🤖 Model Selection Issues: {len(model_selection_issues)}/{len(rows)}")
        if model_selection_issues:
            print("   Tasks with empty/invalid model selections:")
            for task_id, issue_type in model_selection_issues:
                print(f"   - {task_id}: {issue_type}")
        
        # Root cause analysis
        print("\n" + "=" * 80)
        print("\n🔍 ROOT CAUSE ANALYSIS\n")
        print("=" * 80)
        
        print("\n1️⃣ STYLE DEFAULTING TO 'technical':")
        print("   Location: src/cofounder_agent/services/tasks_db.py:209")
        print("   Code: task_data.get('style', 'technical')")
        print("   Solution: Use Pydantic defaults from schema, don't override in DB layer")
        
        print("\n2️⃣ TONE DEFAULTING TO 'professional':")
        print("   Location: src/cofounder_agent/services/tasks_db.py:210")
        print("   Code: task_data.get('tone', 'professional')")
        print("   Solution: Use Pydantic defaults from schema, don't override in DB layer")
        
        print("\n3️⃣ MODEL SELECTIONS EMPTY:")
        print("   Possible causes:")
        print("   a) UI sends as 'models_by_phase' but DB expects 'model_selections'")
        print("   b) Backend handler correctly maps it, but value is empty")
        print("   c) ModelSelectionPanel not sending data correctly")
        print("   Solution: Check UI payload in browser DevTools Network tab")
        
        print("\n" + "=" * 80)
        print("\n💡 RECOMMENDED FIXES\n")
        print("=" * 80)
        
        print("\n1. Update tasks_db.py add_task() method:")
        print("   - Remove fallback defaults for style/tone")
        print("   - Trust values from Pydantic schema or UI")
        print("   - Only use defaults if value is explicitly None/empty")
        
        print("\n2. Check frontend CreateTaskModal.jsx:")
        print("   - Verify formData.style is being captured correctly")
        print("   - Verify formData.tone is being captured correctly")
        print("   - Verify modelSelection.modelSelections has values")
        
        print("\n3. Add logging to task_routes.py:")
        print("   - Log incoming request.style, request.tone, request.models_by_phase")
        print("   - Log task_data dict before db_service.add_task()")
        
        await conn.close()
        print("\n✅ Diagnostic complete")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(diagnose_metadata_flow())
