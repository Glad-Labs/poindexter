#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test the new PostgreSQL-based StrapiPublisher
"""

import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from src.cofounder_agent.services.strapi_publisher import StrapiPublisher

async def main():
    print("=" * 70)
    print("🧪 Testing PostgreSQL-based StrapiPublisher")
    print("=" * 70)
    
    pub = StrapiPublisher()
    
    # Test async connection
    print("\n📊 Testing async connection to PostgreSQL...")
    if await pub.connect():
        print("✅ Connected to PostgreSQL database")
        
        # Test database connection
        success = await pub._async_test_connection()
        if success:
            print(f"✅ Database connection verified")
        
        # Get recent posts
        print("\n📋 Retrieving recent posts...")
        success, posts, msg = await pub.get_posts(limit=5)
        print(msg)
        if posts:
            for post in posts:
                print(f"  - {post['title']} (ID: {post['id']}, Slug: {post['slug']})")
        
        # Try to create a test post
        print("\n📝 Creating test post...")
        result = await pub.create_post(
            title="Test Post from PostgreSQL Publisher",
            content="This is a test post created via the new async PostgreSQL publisher using asyncpg.",
            excerpt="Testing the new publisher",
            slug="test-postgres-publisher"
        )
        
        if result.get("success"):
            print(f"✅ {result.get('message')}")
            post_id = result.get("post_id")
            
            if post_id:
                # Verify post was created
                print(f"\n✔️  Verifying post was created...")
                exists, verify_msg = await pub.verify_post_exists(post_id)
                print(f"✅ {verify_msg}")
        else:
            print(f"❌ {result.get('message')}")
        
        await pub.disconnect()
        print("\n" + "=" * 70)
        print("✅ All tests completed successfully!")
        print("=" * 70)
    else:
        print("❌ Failed to connect to PostgreSQL database")
        print("Check your DATABASE_URL and database credentials")
        sys.exit(1)

if __name__ == "__main__":
    asyncio.run(main())
