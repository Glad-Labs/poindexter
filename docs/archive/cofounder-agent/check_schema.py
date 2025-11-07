#!/usr/bin/env python3
import asyncpg
import asyncio
import os

async def check_schema():
    db_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/glad_labs_dev')
    conn = await asyncpg.connect(db_url)
    
    try:
        # Get posts table schema
        schema = await conn.fetch('''
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_name = 'posts'
            ORDER BY ordinal_position
        ''')
        
        print('\n=== Posts Table Schema ===')
        if not schema:
            print('ERROR: posts table not found or empty schema')
            return
        
        for col in schema:
            print(f"  {col['column_name']}: {col['data_type']} (nullable: {col['is_nullable']})")
        
        # Check if table has data
        count = await conn.fetchval('SELECT COUNT(*) FROM posts')
        print(f'\n=== Table Stats ===')
        print(f"  Total posts: {count}")
        
        if count > 0:
            recent = await conn.fetch('SELECT id, title, created_at FROM posts ORDER BY created_at DESC LIMIT 3')
            print(f'\n=== Recent Posts ===')
            for post in recent:
                print(f"  ID: {post['id']} | Title: {post['title']} | Created: {post['created_at']}")
    
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(check_schema())
