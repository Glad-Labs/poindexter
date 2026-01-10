#!/usr/bin/env python3
"""
Quick script to create a test content task for approval testing
"""
import asyncio
import asyncpg
import os
import uuid
from datetime import datetime
import json

async def create_test_task():
    """Create a test task directly in the database"""
    
    # Database connection
    DATABASE_URL = "postgresql://postgres:postgres@localhost:5432/glad_labs_dev"
    
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        
        # Task data
        task_id = str(uuid.uuid4())
        now = datetime.utcnow()
        
        task_data = {
            "task_id": task_id,
            "request_type": "blog_post",
            "task_type": "content_generation",
            "status": "completed",  # Mark as completed to show in approval UI
            "topic": "Emerging AI Trends in 2025",
            "style": "professional",
            "tone": "informative",
            "target_length": 1500,
            "content": """# Emerging AI Trends in 2025

The world of artificial intelligence continues to evolve at an unprecedented pace. As we look toward 2025, several key trends are emerging that will shape the future of AI technology and its applications across industries.

## 1. Multimodal AI Systems
One of the most significant trends is the rise of multimodal AI systems that can process and understand multiple types of data simultaneously - text, images, audio, and video. These systems are becoming increasingly sophisticated and practical.

## 2. AI in Healthcare
The healthcare industry is witnessing unprecedented adoption of AI for diagnostics, drug discovery, and personalized treatment plans. Machine learning models are now capable of detecting diseases earlier and with greater accuracy than ever before.

## 3. Edge AI and On-Device Processing
As privacy concerns grow, more companies are moving AI processing to the edge - directly on devices rather than relying on cloud servers. This trend improves privacy, reduces latency, and enables real-time processing.

## 4. AI Safety and Ethics
With greater adoption comes greater responsibility. Organizations are investing heavily in AI safety, ethics committees, and responsible AI frameworks to ensure these powerful tools are used responsibly.

## 5. Specialized AI Models
Rather than relying solely on general-purpose models, we're seeing a shift toward specialized AI models trained for specific industries and use cases, delivering better performance and efficiency.

## Conclusion
The future of AI in 2025 looks promising, with significant advances in capability, accessibility, and responsible deployment. Businesses that stay ahead of these trends will be better positioned to leverage AI for competitive advantage.""",
            "excerpt": "Emerging trends shaping artificial intelligence in 2025, from multimodal systems to edge AI and ethical frameworks.",
            "featured_image_prompt": "A futuristic visualization of AI technology with neural networks and digital landscape",
            "featured_image_url": "https://images.pexels.com/photos/8386441/pexels-photo-8386441.jpeg?auto=compress&cs=tinysrgb&w=600",
            "publish_mode": "manual",
            "tags": json.dumps(["AI", "technology", "trends", "2025", "artificial-intelligence"]),
            "task_metadata": json.dumps({
                "quality_score": 8.5,
                "estimated_read_time": "6 minutes"
            }),
            "model_used": "ollama",
            "quality_score": 85,
            "progress": json.dumps({"stage": "completed", "percentage": 100}),
            "created_at": now,
            "updated_at": now,
            "completed_at": now,
            "approval_status": "pending",
            "seo_title": "Emerging AI Trends 2025: What to Watch",
            "seo_description": "Discover the top AI trends shaping 2025, from multimodal systems to edge AI and responsible innovation.",
            "seo_keywords": "AI trends, artificial intelligence, machine learning, 2025, technology",
            "primary_keyword": "AI trends 2025",
            "target_audience": "Tech professionals",
            "category": "technology",
            "stage": "completed",
            "percentage": 100,
            "message": "Content generation completed successfully",
            "model_selections": json.dumps({
                "research": "ollama",
                "outline": "ollama",
                "draft": "ollama",
                "assess": "ollama",
                "refine": "ollama",
                "finalize": "ollama"
            }),
            "quality_preference": "balanced"
        }
        
        # Insert task
        insert_query = """
        INSERT INTO content_tasks (
            task_id, request_type, task_type, status, topic, style, tone, 
            target_length, content, excerpt, featured_image_prompt, 
            featured_image_url, publish_mode, tags, task_metadata, model_used,
            quality_score, progress, created_at, updated_at, completed_at,
            approval_status, seo_title, seo_description, seo_keywords,
            primary_keyword, target_audience, category, stage, percentage, message,
            model_selections, quality_preference
        ) VALUES (
            $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15,
            $16, $17, $18, $19, $20, $21, $22, $23, $24, $25, $26, $27, $28,
            $29, $30, $31, $32, $33
        )
        """
        
        values = [
            task_data["task_id"],
            task_data["request_type"],
            task_data["task_type"],
            task_data["status"],
            task_data["topic"],
            task_data["style"],
            task_data["tone"],
            task_data["target_length"],
            task_data["content"],
            task_data["excerpt"],
            task_data["featured_image_prompt"],
            task_data["featured_image_url"],
            task_data["publish_mode"],
            task_data["tags"],
            task_data["task_metadata"],
            task_data["model_used"],
            task_data["quality_score"],
            task_data["progress"],
            task_data["created_at"],
            task_data["updated_at"],
            task_data["completed_at"],
            task_data["approval_status"],
            task_data["seo_title"],
            task_data["seo_description"],
            task_data["seo_keywords"],
            task_data["primary_keyword"],
            task_data["target_audience"],
            task_data["category"],
            task_data["stage"],
            task_data["percentage"],
            task_data["message"],
            task_data["model_selections"],
            task_data["quality_preference"],
        ]
        
        await conn.execute(insert_query, *values)
        
        print("✅ Test task created successfully!")
        print(f"Task ID: {task_id}")
        print(f"Status: {task_data['status']}")
        print(f"Approval Status: {task_data['approval_status']}")
        print(f"Topic: {task_data['topic']}")
        print("\nYou can now:")
        print("1. Go to Oversight Hub (http://localhost:3001/tasks)")
        print("2. Select this task to view details")
        print("3. Click 'Approve' to test the approval workflow")
        
        await conn.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(create_test_task())
