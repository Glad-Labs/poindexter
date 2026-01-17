#!/usr/bin/env python3
"""
Database Migration Runner
Applies SQL migrations from the migrations/ directory to the PostgreSQL database.
"""

import os
import sys
import psycopg2
import glob
from pathlib import Path
from urllib.parse import urlparse
from dotenv import load_dotenv

# Load .env.local from project root
env_path = Path(__file__).parent.parent.parent.parent / '.env.local'
if env_path.exists():
    load_dotenv(env_path)
    print(f"📄 Loaded environment from: {env_path}")
else:
    print(f"⚠️  .env.local not found at {env_path}")
    print(f"   Checking for environment variables instead...")

def get_database_url():
    """Get DATABASE_URL from environment"""
    db_url = os.getenv('DATABASE_URL')
    if not db_url:
        print("❌ DATABASE_URL environment variable not set")
        print("Please set DATABASE_URL in .env.local or as an environment variable")
        sys.exit(1)
    return db_url

def parse_db_url(db_url):
    """Parse PostgreSQL connection URL"""
    parsed = urlparse(db_url)
    return {
        'host': parsed.hostname,
        'port': parsed.port or 5432,
        'database': parsed.path.lstrip('/'),
        'user': parsed.username,
        'password': parsed.password,
    }

def apply_migrations():
    """Apply all SQL migration files"""
    db_url = get_database_url()  # Use environment variable, not hardcoded!
    db_config = parse_db_url(db_url)
    
    print("🔄 Applying database migrations...")
    print(f"📍 Target: {db_config['host']}:{db_config['port']}/{db_config['database']}")
    
    # Connect to database
    try:
        conn = psycopg2.connect(**db_config)
        cursor = conn.cursor()
    except Exception as e:
        print(f"❌ Failed to connect to database: {e}")
        sys.exit(1)
    
    # Get migrations directory
    migrations_dir = Path(__file__).parent
    migration_files = sorted(glob.glob(str(migrations_dir / "*.sql")))
    
    if not migration_files:
        print("⚠️  No migration files found")
        cursor.close()
        conn.close()
        return
    
    # Define migration order (critical for schema dependencies)
    migration_order = [
        "001_initial_schema.sql",           # Create base tables first
        "001_add_missing_task_columns.sql", # Add columns to existing tables
        "002_quality_evaluation.sql",
        "002a_cost_logs_table.sql",
        "003_training_data_tables.sql",
        "004_writing_samples.sql",
        "005_add_writing_style_id.sql",
        "006_add_all_required_columns.sql", # Add all columns needed by tasks_db.py
        "007_consolidate_to_single_tasks_table.sql",  # Remove FK constraint, use content_tasks standalone
        "008_create_cms_tables.sql",  # Create posts, categories, tags tables for publishing
    ]
    
    # Sort migrations by the defined order, then any others alphabetically
    ordered_migrations = []
    for migration_name in migration_order:
        for migration_file in migration_files:
            if migration_file.endswith(migration_name):
                ordered_migrations.append(migration_file)
                migration_files.remove(migration_file)
                break
    
    # Add any remaining migrations (not in the order list) at the end
    ordered_migrations.extend(sorted(migration_files))
    
    migration_files = ordered_migrations
    
    # Apply each migration
    for migration_file in migration_files:
        migration_name = Path(migration_file).name
        print(f"\n📝 Applying migration: {migration_name}")
        
        try:
            with open(migration_file, 'r') as f:
                sql = f.read()
            
            cursor.execute(sql)
            conn.commit()
            print(f"✅ Migration completed: {migration_name}")
        except Exception as e:
            conn.rollback()
            print(f"❌ Migration failed: {migration_name}")
            print(f"   Error: {e}")
            cursor.close()
            conn.close()
            sys.exit(1)
    
    cursor.close()
    conn.close()
    print("\n✅ All database migrations applied successfully!")

if __name__ == '__main__':
    apply_migrations()
