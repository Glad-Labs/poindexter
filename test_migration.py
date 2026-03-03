#!/usr/bin/env python
"""Test script to verify the migration and database schema."""
import asyncio
import os
from sqlalchemy import create_engine, text

async def test_migration():
    """Test database connection and check for migration columns."""
    # Get database URL from environment
    db_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/glad_labs_dev')
    
    try:
        # Create synchronous engine for testing
        engine = create_engine(db_url)
        
        with engine.connect() as conn:
            print("✅ Database connected successfully!")
            
            # Check if workflow_executions table exists and has our new columns
            result = conn.execute(text("""
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'workflow_executions'
                ORDER BY ordinal_position
            """))
            
            columns = [row[0] for row in result.fetchall()]
            print(f"\n📊 Columns in workflow_executions table ({len(columns)} total):")
            for col in columns:
                print(f"  - {col}")
            
            # Check specifically for our new columns
            has_selected_model = 'selected_model' in columns
            has_execution_mode = 'execution_mode' in columns
            
            print(f"\n✨ Migration Status:")
            print(f"  - selected_model column: {'✅ YES' if has_selected_model else '❌ NO'}")
            print(f"  - execution_mode column: {'✅ YES' if has_execution_mode else '❌ NO'}")
            
            if has_selected_model and has_execution_mode:
                print("\n✨ Migration appears to have been applied successfully!")
            else:
                print("\n⚠️ Migration may not have been applied. Check backend startup logs.")
            
            # Get recent workflow executions
            print("\n📈 Recent workflow executions:")
            result = conn.execute(text("""
                SELECT 
                    execution_id, 
                    selected_model, 
                    execution_mode, 
                    created_at
                FROM workflow_executions
                ORDER BY created_at DESC
                LIMIT 5
            """))
            
            executions = result.fetchall()
            if executions:
                for execution in executions:
                    print(f"  - ID: {execution[0]}")
                    print(f"    Model: {execution[1]}")
                    print(f"    Mode: {execution[2]}")
                    print(f"    Created: {execution[3]}")
                    print()
            else:
                print("  (No recent executions found)")
                
    except Exception as e:
        print(f"❌ Error connecting to database: {e}")
        print(f"   Make sure PostgreSQL is running and DATABASE_URL is set correctly")
        print(f"   Current DATABASE_URL: {db_url}")

if __name__ == '__main__':
    asyncio.run(test_migration())
