#!/usr/bin/env python3
"""Check if blog posts are being published to Strapi"""
import asyncio
import json
import psycopg

async def check_strapi_posts():
    """Check published posts in Strapi database"""
    conn_string = "postgresql://postgres:postgres@localhost:5432/strapi_dev"
    
    try:
        async with await psycopg.AsyncConnection.connect(conn_string) as conn:
            # Get recent posts
            query = """
                SELECT 
                    id, 
                    title, 
                    slug,
                    description,
                    body,
                    published_at,
                    created_at
                FROM posts_posts 
                ORDER BY created_at DESC 
                LIMIT 10
            """
            
            print("=" * 100)
            print("RECENT POSTS IN STRAPI")
            print("=" * 100)
            
            async with conn.cursor() as cur:
                try:
                    await cur.execute(query)
                    posts = await cur.fetchall()
                    
                    if not posts:
                        print("❌ No posts found in Strapi database")
                    else:
                        for i, post_row in enumerate(posts, 1):
                            post_id, title, slug, description, body, published_at, created_at = post_row
                            print(f"\n[Post {i}]")
                            print(f"  ID: {post_id}")
                            print(f"  Title: {title}")
                            print(f"  Slug: {slug}")
                            print(f"  Published: {published_at}")
                            print(f"  Created: {created_at}")
                            if body:
                                body_preview = body[:200] if isinstance(body, str) else str(body)[:200]
                                print(f"  Body Preview: {body_preview}...")
                            if description:
                                print(f"  Description: {description[:100]}...")
                except Exception as e:
                    print(f"Error querying posts_posts table: {e}")
                    print("\nTrying alternative table names...")
                    
                    # Try alternative table names
                    for table_name in ['posts', 'strapi_core_utils_relations_table', 'core_store']:
                        try:
                            query = f"SELECT COUNT(*) as count FROM {table_name}"
                            await cur.execute(query)
                            result = await cur.fetchone()
                            print(f"  Table '{table_name}': {result}")
                        except:
                            pass
            
            print("\n" + "=" * 100)
            print("ALL TABLES IN STRAPI DATABASE")
            print("=" * 100)
            
            query = """
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name
            """
            
            async with conn.cursor() as cur:
                await cur.execute(query)
                tables = await cur.fetchall()
                for table in tables:
                    print(f"  - {table[0]}")
            
    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(check_strapi_posts())
