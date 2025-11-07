#!/usr/bin/env python3
"""Test Strapi GraphQL support"""

import httpx
import json
import asyncio

async def test_graphql():
    async with httpx.AsyncClient() as client:
        # Test GraphQL endpoint
        print("Testing GraphQL endpoint...")
        
        query = """
        mutation CreatePost {
          createPost(data: {
            title: "Test Post from GraphQL"
            slug: "test-graphql"
            content: "This is a test post from GraphQL"
            excerpt: "Test"
            publishedAt: "2025-11-06T00:00:00Z"
          }) {
            data {
              id
              attributes {
                title
                slug
                publishedAt
              }
            }
          }
        }
        """
        
        try:
            response = await client.post(
                "http://localhost:1337/graphql",
                json={"query": query},
                headers={"Authorization": "Bearer 1cdef4eb369677d03e8721869670bb1d2497dbe39be92f8287bb2a61238451f4aec7eaeccb8e65886eb6939d814bec8701992176b6da2475016d037c8d0ed1209cb3028b56b676482cb813474a767a87422f0a7dd3458730b2ae6d24318573a56c0e3ccbf5fc364ec92eda0e65f11d3c6924e4c98f1187afd07d626f287ad61d"},
                timeout=10.0
            )
            
            print(f"Status: {response.status_code}")
            print(f"Response: {response.text}")
            
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(test_graphql())
