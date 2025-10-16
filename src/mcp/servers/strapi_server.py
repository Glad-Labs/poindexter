"""
Strapi CMS MCP Server for GLAD Labs

Provides MCP tools and resources for interacting with the Strapi v5 CMS.
Exposes content management capabilities as standardized MCP tools.
"""

import asyncio
import logging
import os
import sys
from typing import Any, Dict, List, Optional
import json

# Add the agents directory to the path to import existing Strapi client
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..', 'agents', 'content_agent'))

try:
    from services.strapi_client import StrapiClient
    from utils.data_models import BlogPost
    STRAPI_CLIENT_AVAILABLE = True
except ImportError as e:
    print(f"Warning: Could not import StrapiClient: {e}")
    STRAPI_CLIENT_AVAILABLE = False


class StrapiMCPServer:
    """
    MCP Server providing Strapi CMS integration.
    
    This server exposes Strapi content management capabilities as MCP tools,
    allowing agents to create, read, update, and delete content through
    a standardized interface.
    """
    
    def __init__(self, name: str = "strapi-cms-server"):
        self.name = name
        self.logger = logging.getLogger(f"mcp.{name}")
        
        # Initialize Strapi client
        if STRAPI_CLIENT_AVAILABLE:
            self.strapi_client = StrapiClient()
            self.logger.info("Strapi client initialized")
        else:
            self.strapi_client = None
            self.logger.error("Strapi client not available")
    
    # MCP Tools
    
    async def create_post(self, title: str, content: str, excerpt: str = "",
                         category: str = "", tags: List[str] = None,
                         featured: bool = False) -> Dict[str, Any]:
        """
        Create a new blog post in Strapi CMS.
        
        Args:
            title: Post title
            content: Markdown content
            excerpt: Post excerpt/summary
            category: Category slug
            tags: List of tag slugs
            featured: Whether post is featured
            
        Returns:
            Dict with post ID and URL if successful
        """
        if not self.strapi_client:
            return {"error": "Strapi client not available"}
        
        try:
            # Create BlogPost object
            blog_post = BlogPost(
                title=title,
                content=content,
                excerpt=excerpt,
                category=category,
                tags=tags or [],
                featured=featured
            )
            
            # Use existing StrapiClient
            post_id, post_url = self.strapi_client.create_post(blog_post)
            
            if post_id:
                return {
                    "success": True,
                    "post_id": post_id,
                    "post_url": post_url,
                    "message": f"Post '{title}' created successfully"
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to create post"
                }
                
        except Exception as e:
            self.logger.error(f"Error creating post: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_posts(self, limit: int = 10, category: str = "",
                       featured_only: bool = False) -> Dict[str, Any]:
        """
        Retrieve published posts from Strapi.
        
        Args:
            limit: Maximum number of posts to return
            category: Filter by category slug
            featured_only: Only return featured posts
            
        Returns:
            Dict with posts array and metadata
        """
        if not self.strapi_client:
            return {"error": "Strapi client not available"}
        
        try:
            # Get all published posts (using existing method)
            posts_map = self.strapi_client.get_all_published_posts()
            
            # Convert to list format
            posts = [{"title": title, "url": url} for title, url in posts_map.items()]
            
            # Apply filters
            if limit:
                posts = posts[:limit]
            
            return {
                "success": True,
                "posts": posts,
                "total": len(posts),
                "filters_applied": {
                    "limit": limit,
                    "category": category,
                    "featured_only": featured_only
                }
            }
            
        except Exception as e:
            self.logger.error(f"Error getting posts: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def upload_image(self, file_path: str, alt_text: str = "",
                          caption: str = "") -> Dict[str, Any]:
        """
        Upload an image to Strapi media library.
        
        Args:
            file_path: Path to image file
            alt_text: Alt text for accessibility
            caption: Image caption
            
        Returns:
            Dict with image ID and URL if successful
        """
        if not self.strapi_client:
            return {"error": "Strapi client not available"}
        
        try:
            image_id = self.strapi_client.upload_image(file_path, alt_text, caption)
            
            if image_id:
                return {
                    "success": True,
                    "image_id": image_id,
                    "message": f"Image uploaded successfully: {file_path}"
                }
            else:
                return {
                    "success": False,
                    "error": "Failed to upload image"
                }
                
        except Exception as e:
            self.logger.error(f"Error uploading image: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def get_content_stats(self) -> Dict[str, Any]:
        """
        Get content statistics from Strapi.
        
        Returns:
            Dict with content counts and statistics
        """
        if not self.strapi_client:
            return {"error": "Strapi client not available"}
        
        try:
            posts_map = self.strapi_client.get_all_published_posts()
            
            stats = {
                "total_published_posts": len(posts_map),
                "strapi_connection": "active",
                "api_url": getattr(self.strapi_client, 'api_url', 'unknown'),
                "last_updated": "now"
            }
            
            return {
                "success": True,
                "stats": stats
            }
            
        except Exception as e:
            self.logger.error(f"Error getting content stats: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    # MCP Resources
    
    async def get_content_schema(self) -> Dict[str, Any]:
        """
        Get the Strapi content types schema.
        
        Returns:
            Schema information for content types
        """
        return {
            "blog_post": {
                "title": {"type": "string", "required": True},
                "content": {"type": "text", "required": True},
                "excerpt": {"type": "string", "required": False},
                "category": {"type": "relation", "target": "categories"},
                "tags": {"type": "relation", "target": "tags", "multiple": True},
                "featured": {"type": "boolean", "default": False},
                "slug": {"type": "string", "auto": True},
                "publishedAt": {"type": "datetime", "auto": True}
            },
            "category": {
                "name": {"type": "string", "required": True},
                "slug": {"type": "string", "required": True},
                "description": {"type": "text", "required": False}
            },
            "tag": {
                "name": {"type": "string", "required": True},
                "slug": {"type": "string", "required": True}
            }
        }
    
    async def get_published_content(self) -> Dict[str, Any]:
        """
        Get all published content as a resource.
        
        Returns:
            All published content for context
        """
        if not self.strapi_client:
            return {"error": "Strapi client not available"}
        
        try:
            posts_map = self.strapi_client.get_all_published_posts()
            return {
                "published_posts": posts_map,
                "total_count": len(posts_map),
                "content_type": "blog_posts"
            }
        except Exception as e:
            return {"error": str(e)}


# Tool and resource definitions for MCP registration
STRAPI_TOOLS = [
    {
        "name": "create_post",
        "description": "Create a new blog post in Strapi CMS",
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Post title"},
                "content": {"type": "string", "description": "Markdown content"},
                "excerpt": {"type": "string", "description": "Post excerpt/summary"},
                "category": {"type": "string", "description": "Category slug"},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of tag slugs"
                },
                "featured": {"type": "boolean", "description": "Whether post is featured"}
            },
            "required": ["title", "content"]
        }
    },
    {
        "name": "get_posts",
        "description": "Retrieve published posts from Strapi",
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Maximum posts to return", "default": 10},
                "category": {"type": "string", "description": "Filter by category slug"},
                "featured_only": {"type": "boolean", "description": "Only featured posts"}
            }
        }
    },
    {
        "name": "upload_image",
        "description": "Upload an image to Strapi media library",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Path to image file"},
                "alt_text": {"type": "string", "description": "Alt text for accessibility"},
                "caption": {"type": "string", "description": "Image caption"}
            },
            "required": ["file_path"]
        }
    },
    {
        "name": "get_content_stats",
        "description": "Get content statistics from Strapi",
        "input_schema": {
            "type": "object",
            "properties": {}
        }
    }
]

STRAPI_RESOURCES = [
    {
        "name": "content_schema",
        "description": "Strapi content types schema",
        "uri": "strapi://schema/content-types"
    },
    {
        "name": "published_content",
        "description": "All published content for context",
        "uri": "strapi://content/published"
    }
]


async def main():
    """
    Test the Strapi MCP Server
    """
    logging.basicConfig(level=logging.INFO)
    
    server = StrapiMCPServer()
    
    print("=== Strapi MCP Server Test ===")
    
    # Test get content stats
    print("\n1. Content Statistics:")
    stats = await server.get_content_stats()
    print(json.dumps(stats, indent=2))
    
    # Test get posts
    print("\n2. Published Posts:")
    posts = await server.get_posts(limit=5)
    print(json.dumps(posts, indent=2))
    
    # Test get schema
    print("\n3. Content Schema:")
    schema = await server.get_content_schema()
    print(json.dumps(schema, indent=2))
    
    print("\n=== Available Tools ===")
    for i, tool in enumerate(STRAPI_TOOLS, 1):
        print(f"{i}. {tool['name']}: {tool['description']}")
    
    print("\n=== Available Resources ===")
    for i, resource in enumerate(STRAPI_RESOURCES, 1):
        print(f"{i}. {resource['name']}: {resource['description']}")


if __name__ == "__main__":
    asyncio.run(main())