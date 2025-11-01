#!/usr/bin/env python3
"""
Test PostgreSQL connection to glad_labs_dev database

Run: python scripts/test_postgres_connection.py
"""

import os
import sys
import psycopg2
from psycopg2 import sql, Error

# Try both .env variations
DATABASE_URL = os.getenv("DATABASE_URL", "")
POSTGRES_HOST = os.getenv("POSTGRES_HOST", "localhost")
POSTGRES_PORT = os.getenv("POSTGRES_PORT", "5432")
POSTGRES_USER = os.getenv("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "postgres")  # Default to 'postgres' if not set
POSTGRES_DB = os.getenv("POSTGRES_DB", "glad_labs_dev")

print("=" * 70)
print("PostgreSQL Connection Test")
print("=" * 70)
print()

# Show configuration (hide password)
print("Configuration:")
print(f"  Host: {POSTGRES_HOST}")
print(f"  Port: {POSTGRES_PORT}")
print(f"  User: {POSTGRES_USER}")
print(f"  Password: {'***' if POSTGRES_PASSWORD else '(empty)'}")
print(f"  Database: {POSTGRES_DB}")
print(f"  DATABASE_URL: {DATABASE_URL[:50]}..." if DATABASE_URL else "  DATABASE_URL: (not set)")
print()

# Attempt connection
try:
    print("Attempting connection...")
    
    if DATABASE_URL:
        # Use DATABASE_URL if provided
        connection = psycopg2.connect(DATABASE_URL)
        print("✅ Connected using DATABASE_URL")
    else:
        # Use individual parameters
        connection = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            database=POSTGRES_DB
        )
        print("✅ Connected using individual parameters")
    
    print()
    print("=" * 70)
    print("Database Information")
    print("=" * 70)
    
    cursor = connection.cursor()
    
    # Get database info
    cursor.execute("""
        SELECT version();
    """)
    version = cursor.fetchone()[0]
    print(f"PostgreSQL Version: {version.split(',')[0]}")
    print()
    
    # Get current database
    cursor.execute("""
        SELECT current_database();
    """)
    current_db = cursor.fetchone()[0]
    print(f"Current Database: {current_db}")
    print()
    
    # List tables
    cursor.execute("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public'
        ORDER BY table_name;
    """)
    tables = cursor.fetchall()
    
    print("Tables in database:")
    if tables:
        for table in tables:
            cursor.execute(f"""
                SELECT COUNT(*) FROM "{table[0]}";
            """)
            count = cursor.fetchone()[0]
            print(f"  - {table[0]} ({count} rows)")
    else:
        print("  (no tables found)")
    print()
    
    # Connection summary
    print("=" * 70)
    print("✅ Connection Successful!")
    print("=" * 70)
    print()
    print("Connection Info:")
    print(f"  Status: Connected")
    print(f"  Database: {current_db}")
    print(f"  Tables: {len(tables)}")
    print(f"  User: {connection.get_dsn_parameters()['user']}")
    print()
    
    cursor.close()
    connection.close()
    
except Error as e:
    print("=" * 70)
    print("❌ Connection Failed!")
    print("=" * 70)
    print()
    print(f"Error: {e}")
    print()
    
    # Provide troubleshooting tips
    print("Troubleshooting Tips:")
    print()
    print("1. Make sure PostgreSQL is running:")
    print("   - Windows: Services → PostgreSQL")
    print("   - macOS: brew services start postgresql")
    print("   - Linux: sudo systemctl start postgresql")
    print()
    print("2. Check connection parameters:")
    print(f"   Host: {POSTGRES_HOST}")
    print(f"   Port: {POSTGRES_PORT}")
    print(f"   User: {POSTGRES_USER}")
    print(f"   Database: {POSTGRES_DB}")
    print()
    print("3. Verify database exists:")
    print("   psql -U postgres -c 'CREATE DATABASE glad_labs_dev;'")
    print()
    print("4. Set DATABASE_URL environment variable:")
    print(f"   DATABASE_URL=postgresql://{POSTGRES_USER}:password@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}")
    print()
    
    sys.exit(1)

except Exception as e:
    print("=" * 70)
    print(f"❌ Unexpected Error: {type(e).__name__}")
    print("=" * 70)
    print(f"Error: {e}")
    sys.exit(1)
