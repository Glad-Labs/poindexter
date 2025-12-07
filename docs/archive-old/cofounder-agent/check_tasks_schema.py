#!/usr/bin/env python
import psycopg2

conn = psycopg2.connect('postgresql://postgres:postgres@localhost:5432/glad_labs_dev')
cur = conn.cursor()

cur.execute("""
SELECT column_name, data_type 
FROM information_schema.columns 
WHERE table_name='tasks' 
ORDER BY ordinal_position
""")

print("Tasks table schema:")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]}")

cur.close()
conn.close()
