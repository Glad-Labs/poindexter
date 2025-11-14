"""
Seed CMS database with sample content.

Run with:
    python seed_cms_data.py
"""

import os
from datetime import datetime, timedelta
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()

def seed_data():
    """Insert sample posts, categories, tags."""
    
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/glad_labs_dev')
    
    if '+asyncpg' in database_url:
        database_url = database_url.replace('postgresql+asyncpg://', 'postgresql://')
    
    print("🌱 Seeding CMS database with sample content...\n")
    
    engine = create_engine(database_url, echo=False)
    
    with engine.connect() as conn:
        # Insert categories
        print("📂 Creating categories...")
        categories = [
            ("Technology", "technology", "AI, automation, and tech trends"),
            ("Business", "business", "Business strategy and operations"),
            ("Insights", "insights", "Market insights and analysis"),
        ]
        
        for name, slug, desc in categories:
            conn.execute(text("""
                INSERT INTO categories (name, slug, description)
                VALUES (:name, :slug, :desc)
                ON CONFLICT DO NOTHING
            """), {"name": name, "slug": slug, "desc": desc})
        
        print(f"   ✅ {len(categories)} categories")
        
        # Insert tags
        print("🏷️  Creating tags...")
        tags = [
            ("AI", "ai", "Artificial Intelligence", "#FF6B6B"),
            ("Automation", "automation", "Process Automation", "#4ECDC4"),
            ("Future of Work", "future-of-work", "The changing workplace", "#45B7D1"),
            ("Business", "business", "Business topics", "#96CEB4"),
            ("Strategy", "strategy", "Business strategy", "#FFEAA7"),
        ]
        
        for name, slug, desc, color in tags:
            conn.execute(text("""
                INSERT INTO tags (name, slug, description, color)
                VALUES (:name, :slug, :desc, :color)
                ON CONFLICT DO NOTHING
            """), {"name": name, "slug": slug, "desc": desc, "color": color})
        
        print(f"   ✅ {len(tags)} tags")
        
        # Insert sample posts
        print("📄 Creating sample posts...")
        
        now = datetime.now()
        posts = [
            {
                "title": "The Future of AI in Business",
                "slug": "future-of-ai-in-business",
                "excerpt": "AI is transforming how businesses operate. From automating routine tasks to enabling data-driven decision making, AI has become essential.",
                "category": "Technology",
                "tags": ["AI", "Future of Work"],
                "featured": True,
                "published_at": now - timedelta(days=3),
            },
            {
                "title": "Automation: Boosting Productivity",
                "slug": "automation-boosting-productivity",
                "excerpt": "Automation is no longer a luxury—it's a necessity. Organizations that automate repetitive tasks see significant improvements.",
                "category": "Business",
                "tags": ["Automation", "Business"],
                "featured": False,
                "published_at": now - timedelta(days=2),
            },
            {
                "title": "Market Trends Q4 2025",
                "slug": "market-trends-q4-2025",
                "excerpt": "As we head into the final quarter of 2025, several market trends are reshaping the business landscape.",
                "category": "Insights",
                "tags": ["Strategy", "Business"],
                "featured": True,
                "published_at": now - timedelta(days=1),
            },
        ]
        
        for post_data in posts:
            # Insert post
            result = conn.execute(text("""
                INSERT INTO posts (title, slug, content, excerpt, featured, published_at, category_id,
                                   seo_title, seo_description, seo_keywords, created_at, updated_at)
                VALUES (:title, :slug, :content, :excerpt, :featured, :published_at,
                        (SELECT id FROM categories WHERE slug = :cat_slug),
                        :seo_title, :seo_desc, :seo_keys, NOW(), NOW())
                ON CONFLICT DO NOTHING
                RETURNING id
            """), {
                "title": post_data["title"],
                "slug": post_data["slug"],
                "content": f"# {post_data['title']}\n\n{post_data['excerpt']}\n\n## Key Points\n- Point 1\n- Point 2\n- Point 3",
                "excerpt": post_data["excerpt"],
                "featured": post_data["featured"],
                "published_at": post_data["published_at"],
                "cat_slug": post_data["category"].lower(),
                "seo_title": f"{post_data['title']} | Glad Labs",
                "seo_desc": post_data["excerpt"],
                "seo_keys": ", ".join(post_data["tags"]),
            })
            
            post_id = result.scalar()
            if post_id:
                # Link tags
                for tag_name in post_data["tags"]:
                    conn.execute(text("""
                        INSERT INTO post_tags (post_id, tag_id)
                        SELECT :post_id, id FROM tags WHERE name = :tag_name
                        ON CONFLICT DO NOTHING
                    """), {"post_id": post_id, "tag_name": tag_name})
        
        print(f"   ✅ {len(posts)} posts")
        
        conn.commit()
    
    # Verify
    print("\n📊 Verification:")
    with engine.connect() as conn:
        categories_count = conn.execute(text("SELECT COUNT(*) FROM categories")).scalar()
        tags_count = conn.execute(text("SELECT COUNT(*) FROM tags")).scalar()
        posts_count = conn.execute(text("SELECT COUNT(*) FROM posts")).scalar()
        
        print(f"   Categories: {categories_count}")
        print(f"   Tags: {tags_count}")
        print(f"   Posts: {posts_count}")
    
    print("\n✨ Sample data seeded!")

if __name__ == '__main__':
    try:
        seed_data()
        print("\n✅ Done!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
