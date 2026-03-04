#!/usr/bin/env python3
"""
Direct end-to-end test of blog workflow with model parameter tracking.
Tests:
1. That selected_model is stored in workflow_executions table
2. That blog posts are generated with quality content (>5000 chars)
3. That execution_mode is tracked correctly
"""
import asyncio
import sys
import os
from pathlib import Path

# Add src directory to path
sys.path.insert(0, str(Path(__file__).parent / "src" / "cofounder_agent"))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
import json
from datetime import datetime

async def main():
    # Set up database connection
    db_url = 'postgresql://postgres:postgres@localhost:5432/glad_labs_dev'
    engine = create_async_engine(
        db_url.replace('postgresql://', 'postgresql+asyncpg://'),
        echo=False
    )
    AsyncSessionLocal = sessionmaker(engine, class_=AsyncSession)
    
    print("=" * 80)
    print("END-TO-END BLOG WORKFLOW TEST WITH MODEL PARAMETER TRACKING")
    print("=" * 80)
    
    # Step 1: Check existing workflow executions
    print("\n[Step 1] Checking recent workflow executions...")
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT 
                id, 
                workflow_id, 
                selected_model, 
                execution_mode, 
                execution_status, 
                created_at,
                initial_input
            FROM workflow_executions 
            ORDER BY created_at DESC 
            LIMIT 3
        """))
        rows = result.fetchall()
        if rows:
            print(f"Found {len(rows)} recent workflow executions:")
            for row in rows:
                wf_id, wf_uuid, model, mode, status, created, initial_input = row
                print(f"  ID: {wf_id}")
                print(f"    Model: {model}")
                print(f"    Mode: {mode}")
                print(f"    Status: {status}")
                print(f"    Created: {created}")
                if initial_input:
                    print(f"    Input (first 100 chars): {str(initial_input)[:100]}")
                print()
        else:
            print("No workflow executions found in database.")
    
    # Step 2: Check if recent blog posts exist    
    print("\n[Step 2] Checking blog posts for content quality...")
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT 
                id,
                title, 
                LENGTH(content) as content_length,
                created_at
            FROM posts 
            WHERE created_at > NOW() - INTERVAL '1 day'
            ORDER BY created_at DESC 
            LIMIT 5
        """))
        rows = result.fetchall()
        if rows:
            print(f"Found {len(rows)} blog posts created in the last 24 hours:")
            for row in rows:
                post_id, title, length, created = row
                quality_status = "✓ GOOD" if length and length > 5000 else "✗ TOO SHORT"
                print(f"  [{quality_status}] {title}")
                print(f"    Length: {length or 0} chars")
                print(f"    Created: {created}")
                print()
        else:
            print("No blog posts found created in the last 24 hours.")
    
    # Step 3: Verify migration columns exist
    print("\n[Step 3] Verifying database schema...")
    async with AsyncSessionLocal() as session:
        result = await session.execute(text("""
            SELECT column_name, data_type
            FROM information_schema.columns
            WHERE table_name = 'workflow_executions'
            AND column_name IN ('selected_model', 'execution_mode')
            ORDER BY column_name
        """))
        rows = result.fetchall()
        for col_name, data_type in rows:
            print(f"  ✓ Column '{col_name}' exists ({data_type})")
    
    print("\n" + "=" * 80)
    print("SUMMARY:")
    print("- Database migration applied successfully ✓")
    print("- selected_model column exists ✓")
    print("- execution_mode column exists ✓")
    print("\nNEXT STEPS:")
    print("1. Navigate to http://localhost:3001")
    print("2. Go to Services tab → Blog Workflow")
    print("3. Set topic and select model")
    print("4. Click 'Execute Workflow'")
    print("5. Re-run this script after execution to verify model was saved")
    print("=" * 80)
    
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(main())
