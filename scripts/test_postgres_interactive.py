#!/usr/bin/env python3
"""
Interactive PostgreSQL connection test with password prompt

Run: python scripts/test_postgres_interactive.py
"""

import os
import sys
import getpass
import psycopg2
from psycopg2 import sql, Error

print("=" * 70)
print("PostgreSQL Connection Test - Interactive")
print("=" * 70)
print()

# Get connection parameters
POSTGRES_HOST = input("PostgreSQL Host [localhost]: ").strip() or "localhost"
POSTGRES_PORT = input("PostgreSQL Port [5432]: ").strip() or "5432"
POSTGRES_USER = input("PostgreSQL User [postgres]: ").strip() or "postgres"
POSTGRES_PASSWORD = getpass.getpass("PostgreSQL Password (hidden): ")
POSTGRES_DB = input("Database Name [glad_labs_dev]: ").strip() or "glad_labs_dev"

print()
print("=" * 70)
print("Attempting Connection...")
print("=" * 70)
print()

try:
    connection = psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        database=POSTGRES_DB
    )
    print("✅ Connected successfully!")
    print()
    
    cursor = connection.cursor()
    
    # Get database info
    cursor.execute("SELECT version();")
    version = cursor.fetchone()[0]
    print(f"PostgreSQL Version: {version.split(',')[0]}")
    print()
    
    # Get current database
    cursor.execute("SELECT current_database();")
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
            cursor.execute(f'SELECT COUNT(*) FROM "{table[0]}";')
            count = cursor.fetchone()[0]
            print(f"  - {table[0]} ({count} rows)")
    else:
        print("  (no tables found)")
    print()
    
    # Show connection string for .env
    print("=" * 70)
    print("Connection Successful! 🎉")
    print("=" * 70)
    print()
    print("Add this to your .env file:")
    print(f"DATABASE_URL=postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}")
    print()
    
    cursor.close()
    connection.close()

except Error as e:
    print(f"❌ Connection Failed: {e}")
    print()
    sys.exit(1)

except KeyboardInterrupt:
    print("\n\nCancelled by user.")
    sys.exit(0)
