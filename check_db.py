import sqlite3
import json

# Try different possible database paths
paths = [
    'cms/strapi-v5-backend/.tmp/data.db',
    '.tmp/data.db',
    'src/cofounder_agent/.tmp/data.db'
]

db_path = None
for path in paths:
    try:
        conn = sqlite3.connect(path)
        cursor = conn.cursor()
        cursor.execute('SELECT 1')
        print(f"✅ Connected to {path}")
        db_path = path
        break
    except:
        continue

if not db_path:
    print("❌ Could not find database")
    exit(1)

cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = cursor.fetchall()
print(f"\nAll tables: {[t[0] for t in tables]}")

# Look for tasks table
for table_name, in tables:
    if 'task' in table_name.lower():
        print(f"\n🔍 Found task table: {table_name}")
        cursor.execute(f'PRAGMA table_info({table_name})')
        columns = cursor.fetchall()
        print(f"Columns: {[col[1] for col in columns]}")
        
        cursor.execute(f'SELECT COUNT(*) FROM {table_name}')
        count = cursor.fetchone()[0]
        print(f"Rows: {count}")
        
        # Show recent tasks
        cursor.execute(f'SELECT id, task_name, status, result FROM {table_name} ORDER BY created_at DESC LIMIT 3')
        for row in cursor.fetchall():
            print(f"  {row[0][:8]}... | {row[1]} | {row[2]}")

conn.close()
