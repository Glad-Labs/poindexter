#!/usr/bin/env python3
"""
Clean up Railway PostgreSQL database by dropping old Strapi tables
and preparing for fresh migrations.

Usage:
    python cleanup-railway-db.py
"""

import os
import psycopg2
from psycopg2 import sql
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env.local'))

DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    print("❌ DATABASE_URL not found in .env.local")
    exit(1)

print(f"🔗 Connecting to: {DATABASE_URL.split('@')[1]}")

try:
    conn = psycopg2.connect(DATABASE_URL)
    cursor = conn.cursor()
    
    print("\n📋 Listing all tables...")
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
        ORDER BY table_name;
    """)
    
    tables = [row[0] for row in cursor.fetchall()]
    print(f"Found {len(tables)} tables:")
    for table in tables:
        print(f"  - {table}")
    
    # Identify old Strapi tables
    strapi_patterns = ['strapi', 'documents', 'relations', 'core_store']
    old_tables = [t for t in tables if any(pattern in t.lower() for pattern in strapi_patterns)]
    
    if old_tables:
        print(f"\n⚠️  Found {len(old_tables)} old Strapi tables:")
        for table in old_tables:
            print(f"  - {table}")
        
        response = input("\n🗑️  Delete these old tables? (yes/no): ").strip().lower()
        if response == 'yes':
            # Drop tables with CASCADE to handle foreign keys
            for table in old_tables:
                cursor.execute(sql.SQL("DROP TABLE IF EXISTS {} CASCADE;").format(
                    sql.Identifier(table)
                ))
                print(f"✅ Dropped {table}")
            
            conn.commit()
            print("✅ Old tables dropped successfully")
        else:
            print("⏭️  Skipping table deletion")
    else:
        print("✅ No old Strapi tables found")
    
    # List current tables
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
        ORDER BY table_name;
    """)
    
    current_tables = [row[0] for row in cursor.fetchall()]
    print(f"\n📊 Current tables in database ({len(current_tables)} total):")
    for table in current_tables:
        print(f"  - {table}")
    
    cursor.close()
    conn.close()
    
    print("\n✅ Database cleanup complete!")
    print("📝 Next: Run migrations with: cd src/cofounder_agent && python migrations/apply_migrations.py")

except Exception as e:
    print(f"❌ Error: {e}")
    exit(1)
