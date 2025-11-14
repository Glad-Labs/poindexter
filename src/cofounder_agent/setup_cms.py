"""
Setup script for content CMS database.

Creates content tables and seeds sample data.
Run this once to initialize the CMS:

    python setup_cms.py

This will:
1. Create posts, authors, categories, tags tables
2. Add sample authors
3. Add sample categories
4. Add sample posts
"""

import os
import sys
from datetime import datetime
from uuid import uuid4

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv()

# Add parent directory to path
sys.path.insert(0, os.path.dirname(__file__))

# Import database configuration
from database import get_database_url
from models import Base, Author, Category, Tag, Post

# Use SQLAlchemy synchronously with psycopg2 for setup
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

def setup_cms():
    """Initialize CMS tables and seed sample data."""
    
    print("🔧 Setting up CMS database...")
    
    # Get database URL and convert to synchronous driver (psycopg2 instead of asyncpg)
    database_url = get_database_url()
    if '+asyncpg' in database_url:
        # Convert async driver to sync driver for setup
        sync_database_url = database_url.replace('postgresql+asyncpg://', 'postgresql://')
    else:
        sync_database_url = database_url
    
    print(f"📊 Database URL: {sync_database_url.split('@')[-1]}")
    
    # Create synchronous engine for setup
    engine = create_engine(
        sync_database_url,
        echo=False,
        pool_pre_ping=True,
    )
    
    # Create all tables
    try:
        Base.metadata.create_all(bind=engine)
        print("✅ Tables created")
    except Exception as e:
        print(f"❌ Error creating tables: {e}")
        raise
    
    # Get session
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    
    try:
        # Check if we already have authors (to avoid duplicates)
        existing_authors = db.query(Author).count()
        if existing_authors > 0:
            print(f"⚠️  Database already has {existing_authors} authors. Skipping sample data.")
            return
        
        # Create sample authors
        print("\n📝 Creating sample authors...")
        author1 = Author(
            id=uuid4(),
            name="Sarah Johnson",
            slug="sarah-johnson",
            email="sarah@example.com",
            bio="Content strategist and AI enthusiast. Writes about modern business practices and technology trends.",
            avatar_url="https://i.pravatar.cc/150?img=1"
        )
        author2 = Author(
            id=uuid4(),
            name="Michael Chen",
            slug="michael-chen",
            email="michael@example.com",
            bio="Developer and thought leader. Focuses on emerging technologies and their business impact.",
            avatar_url="https://i.pravatar.cc/150?img=2"
        )
        db.add_all([author1, author2])
        print(f"  ✅ Added {2} authors")
        
        # Create sample categories
        print("📂 Creating sample categories...")
        tech_category = Category(
            id=uuid4(),
            name="Technology",
            slug="technology",
            description="Articles about technology trends, tools, and innovations"
        )
        business_category = Category(
            id=uuid4(),
            name="Business",
            slug="business",
            description="Business strategy, operations, and growth"
        )
        insights_category = Category(
            id=uuid4(),
            name="Insights",
            slug="insights",
            description="Market insights and analysis"
        )
        db.add_all([tech_category, business_category, insights_category])
        print(f"  ✅ Added {3} categories")
        
        # Commit to get IDs
        db.commit()
        
        # Create sample tags
        print("🏷️  Creating sample tags...")
        tag_ai = Tag(
            id=uuid4(),
            name="AI",
            slug="ai",
            description="Artificial Intelligence",
            color="#FF6B6B"
        )
        tag_automation = Tag(
            id=uuid4(),
            name="Automation",
            slug="automation",
            description="Process automation",
            color="#4ECDC4"
        )
        tag_future = Tag(
            id=uuid4(),
            name="Future of Work",
            slug="future-of-work",
            description="The evolving workplace",
            color="#45B7D1"
        )
        db.add_all([tag_ai, tag_automation, tag_future])
        db.commit()
        print(f"  ✅ Added {3} tags")
        
        # Create sample posts
        print("📰 Creating sample posts...")
        post1 = Post(
            id=uuid4(),
            title="The Future of AI in Business",
            slug="future-of-ai-in-business",
            content="""# The Future of AI in Business

Artificial Intelligence is reshaping how businesses operate. From automation to analytics, AI is becoming essential.

## Key Trends

1. **Automation** - Routine tasks are being automated
2. **Analytics** - Better insights from data
3. **Personalization** - Custom experiences for users

The companies that embrace AI early will have competitive advantages.

## Getting Started

- Assess your current processes
- Identify automation opportunities
- Invest in the right tools and talent
""",
            excerpt="AI is transforming business operations. Here's what you need to know.",
            featured_image_url="https://via.placeholder.com/600x400?text=AI+in+Business",
            cover_image_url="https://via.placeholder.com/1200x400?text=AI+in+Business",
            author_id=author1.id,
            category_id=tech_category.id,
            tag_ids=[tag_ai.id, tag_future.id],
            seo_title="The Future of AI in Business | Insights",
            seo_description="Explore how AI is transforming business operations and what you need to know.",
            seo_keywords="AI, business, automation, future",
            status="published",
            published_at=datetime.utcnow(),
            view_count=0
        )
        
        post2 = Post(
            id=uuid4(),
            title="Automation: Boosting Productivity",
            slug="automation-boosting-productivity",
            content="""# Automation: The Path to Higher Productivity

Process automation isn't just about cutting costs—it's about enabling your team to focus on higher-value work.

## Why Automation Matters

- Reduces errors in repetitive tasks
- Frees up employee time
- Improves consistency
- Accelerates workflows

## Common Areas for Automation

1. Data entry and validation
2. Report generation
3. Email workflows
4. Approval processes
5. File management

Start with the most repetitive tasks and measure the impact.
""",
            excerpt="Learn how automation can transform your business processes.",
            featured_image_url="https://via.placeholder.com/600x400?text=Automation",
            cover_image_url="https://via.placeholder.com/1200x400?text=Automation",
            author_id=author2.id,
            category_id=business_category.id,
            tag_ids=[tag_automation.id],
            seo_title="Automation for Higher Productivity",
            seo_description="Discover how process automation can boost your team's productivity.",
            seo_keywords="automation, productivity, efficiency, business",
            status="published",
            published_at=datetime.utcnow(),
            view_count=0
        )
        
        post3 = Post(
            id=uuid4(),
            title="Market Trends Q4 2025",
            slug="market-trends-q4-2025",
            content="""# Market Trends for Q4 2025

As we head into the final quarter, several important trends are shaping the business landscape.

## Key Trends

- **Digital Transformation** continues to accelerate
- **Remote Work** remains flexible but hybrid is gaining traction
- **Sustainability** is becoming a competitive advantage
- **AI Adoption** is mainstream across industries

## What This Means

Companies must stay agile and adapt to these changing dynamics.
""",
            excerpt="Explore the key market trends shaping Q4 2025.",
            featured_image_url="https://via.placeholder.com/600x400?text=Market+Trends",
            cover_image_url="https://via.placeholder.com/1200x400?text=Market+Trends",
            author_id=author1.id,
            category_id=insights_category.id,
            tag_ids=[tag_future.id],
            seo_title="Q4 2025 Market Trends",
            seo_description="Key market trends and insights for Q4 2025.",
            seo_keywords="market trends, business, 2025",
            status="published",
            published_at=datetime.utcnow(),
            view_count=0
        )
        
        db.add_all([post1, post2, post3])
        db.commit()
        print(f"  ✅ Added {3} posts")
        
        print("\n✅ CMS setup complete!")
        print("\n📊 Sample content:")
        print(f"  - Authors: {db.query(Author).count()}")
        print(f"  - Categories: {db.query(Category).count()}")
        print(f"  - Tags: {db.query(Tag).count()}")
        print(f"  - Posts: {db.query(Post).count()}")
        
    finally:
        db.close()

if __name__ == "__main__":
    setup_cms()
