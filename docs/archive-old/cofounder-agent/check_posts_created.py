#!/usr/bin/env python3
import psycopg2

conn = psycopg2.connect('postgresql://postgres:postgres@localhost:5432/glad_labs_dev')
cur = conn.cursor()

# Check latest posts
cur.execute("""
SELECT id, document_id, title, slug, excerpt, featured, created_at, published_at
FROM posts
ORDER BY created_at DESC
LIMIT 5
""")

print("\n📊 Latest posts in database:\n")
for row in cur.fetchall():
    print(f"ID: {row[0]}")
    print(f"  Document ID: {row[1]}")
    print(f"  Title: {row[2]}")
    print(f"  Slug: {row[3]}")
    print(f"  Excerpt: {row[4]}")
    print(f"  Featured: {row[5]}")
    print(f"  Created: {row[6]}")
    print(f"  Published: {row[7]}")
    print()

cur.close()
conn.close()
