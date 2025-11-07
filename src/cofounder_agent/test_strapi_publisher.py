#!/usr/bin/env python3
"""
Test the fixed StrapiPublisher with correct PostgreSQL schema
"""
import asyncio
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(__file__))

from services.strapi_publisher import StrapiPublisher


async def test_publisher():
    """Test the PostgreSQL-based StrapiPublisher"""
    
    print("\n" + "="*60)
    print("🧪 Testing PostgreSQL-based StrapiPublisher")
    print("="*60)
    
    publisher = StrapiPublisher()
    
    # Test 1: Connection
    print("\n✋ Test 1: Database Connection")
    if not await publisher.connect():
        print("❌ FAILED: Could not connect to database")
        return
    print("✅ PASSED: Connected to PostgreSQL")
    
    # Test 2: Connection health check
    print("\n✋ Test 2: Database Health Check")
    async with publisher.pool.acquire() as conn:
        result = await conn.fetchval("SELECT 1")
        if result == 1:
            print("✅ PASSED: Database connection verified")
        else:
            print("❌ FAILED: Database health check failed")
            return
    
    # Test 3: Retrieve existing posts
    print("\n✋ Test 3: Retrieve Existing Posts")
    success, posts, message = await publisher.get_posts(limit=5)
    print(f"  {message}")
    if success:
        print(f"✅ PASSED: Retrieved {len(posts)} posts")
        if posts:
            for post in posts:
                print(f"    - ID: {post['id']} | Title: {post['title']}")
    else:
        print("❌ FAILED: Could not retrieve posts")
        return
    
    # Test 4: Create a test post
    print("\n✋ Test 4: Create Test Post")
    print("  Creating: 'Test Post - Schema Fixed' with UUID-based document_id...")
    
    result = await publisher.create_post(
        title="Test Post - Schema Fixed",
        content="This is a test post created with fixed schema (document_id as UUID, id auto-increment)",
        slug="test-post-schema-fixed",
        excerpt="Test excerpt"
    )
    
    if result["success"]:
        print(f"✅ PASSED: {result['message']}")
        print(f"   Post ID: {result['post_id']}")
        print(f"   Document ID: {result['document_id']}")
        print(f"   Created: {result['created_at']}")
        
        # Test 5: Verify post exists
        print("\n✋ Test 5: Verify Post Exists")
        exists, post_data = await publisher.verify_post_exists("Test Post - Schema Fixed")
        if exists:
            print(f"✅ PASSED: {result['message']}")
            print(f"   Verified Post ID: {post_data['id']}")
        else:
            print("❌ FAILED: Post not found after creation")
    else:
        print(f"❌ FAILED: {result['message']}")
        if 'error' in result:
            print(f"   Error: {result['error']}")
    
    # Cleanup
    await publisher.disconnect()
    print("\n" + "="*60)
    print("✅ Testing Complete")
    print("="*60 + "\n")


if __name__ == "__main__":
    asyncio.run(test_publisher())
