"""
Populate sample blog posts into existing Strapi database tables.

This script works with the existing Strapi schema that's already in glad_labs_dev.
No schema changes needed - just inserts sample content.

Run with:
    python populate_sample_data.py
"""

import os
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, text, select, insert
from sqlalchemy.orm import sessionmaker

# Load environment
load_dotenv()

def get_db_connection():
    """Get synchronous database connection."""
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/glad_labs_dev')
    
    # Convert async URL to sync if needed
    if '+asyncpg' in database_url:
        database_url = database_url.replace('postgresql+asyncpg://', 'postgresql://')
    
    return create_engine(database_url, echo=False)

def populate_data():
    """Insert sample data into existing Strapi tables."""
    
    engine = get_db_connection()
    Session = sessionmaker(bind=engine)
    session = Session()
    
    try:
        print("📝 Populating sample blog post data...")
        
        # Check current table structure
        with engine.connect() as conn:
            # Get list of tables
            result = conn.execute(text("""
                SELECT table_name FROM information_schema.tables 
                WHERE table_schema = 'public' AND table_name = 'posts'
            """))
            if not result.fetchone():
                print("❌ Posts table not found!")
                return
            
            # Get column info
            result = conn.execute(text("""
                SELECT column_name, data_type FROM information_schema.columns 
                WHERE table_name = 'posts' AND table_schema = 'public'
                ORDER BY ordinal_position
            """))
            
            print("\n📊 Posts table structure:")
            for col in result:
                print(f"   {col[0]}: {col[1]}")
        
        # Check if sample data already exists
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT COUNT(*) FROM posts WHERE title LIKE '%AI in Business%'
            """))
            count = result.scalar()
            
            if count > 0:
                print("\n✅ Sample data already exists! Skipping population...")
                return
        
        # Insert sample posts using raw SQL (works with Strapi schema)
        print("\n🚀 Inserting sample posts...")
        
        posts_data = [
            {
                'title': 'The Future of AI in Business',
                'slug': 'future-of-ai-in-business',
                'content': '''# The Future of AI in Business

AI is transforming how businesses operate. From automating routine tasks to enabling data-driven decision making, AI has become essential for competitive advantage.

## Current Trends

- **Automation**: AI is handling routine administrative tasks
- **Analytics**: Advanced analytics for business intelligence
- **Customer Experience**: Personalized recommendations and support

## Getting Started

To start leveraging AI in your business:

1. Identify processes that could be automated
2. Evaluate AI tools for your industry
3. Start with a pilot project
4. Scale based on results

The future belongs to companies that can effectively harness AI technology while maintaining human oversight.''',
                'excerpt': 'AI is transforming business operations. Learn how to get started with AI-driven automation and analytics for your organization.',
                'featured': True,
                'published_at': datetime(2025, 11, 13, 10, 0, 0),
            },
            {
                'title': 'Automation: Boosting Productivity',
                'slug': 'automation-boosting-productivity',
                'content': '''# Automation: Boosting Productivity

Automation is no longer a luxury—it's a necessity. Organizations that automate repetitive tasks see significant improvements in productivity and employee satisfaction.

## Benefits of Automation

- **Time Savings**: Eliminate hours of manual work daily
- **Consistency**: Automated processes are reliable and repeatable
- **Cost Reduction**: Lower labor costs through efficiency gains
- **Error Reduction**: Fewer human errors in critical processes

## Common Use Cases

1. **Data Entry**: Automatically capture and process data
2. **Email Management**: Auto-sort and prioritize messages
3. **Report Generation**: Automatically compile data into reports
4. **Customer Service**: Chatbots for common questions

## Implementation Tips

- Start small with high-impact processes
- Document your current workflows
- Choose tools that integrate with existing systems
- Train your team on new processes

Automation frees your team to focus on strategic work that drives real business value.''',
                'excerpt': 'Discover how automation can boost productivity, reduce costs, and free your team to focus on strategic initiatives.',
                'featured': False,
                'published_at': datetime(2025, 11, 12, 14, 30, 0),
            },
            {
                'title': 'Market Trends Q4 2025',
                'slug': 'market-trends-q4-2025',
                'content': '''# Market Trends Q4 2025

As we head into the final quarter of 2025, several market trends are reshaping the business landscape.

## Key Market Dynamics

### Technology Adoption
Companies are increasingly moving to cloud-first strategies. The adoption rate of AI and machine learning continues to accelerate across all industries.

### Remote Work Evolution
Remote work has matured from a temporary necessity to a permanent workplace model. Hybrid arrangements are now the standard.

### Sustainability Focus
Environmental, Social, and Governance (ESG) initiatives are moving from optional to mandatory for competitive organizations.

## Industry-Specific Trends

- **Finance**: Fintech disruption and digital banking acceleration
- **Retail**: Omnichannel strategies becoming essential
- **Healthcare**: Telehealth adoption reaching new heights
- **Manufacturing**: Digital transformation and Industry 4.0 initiatives

## What This Means for Your Business

Organizations should focus on:
- Continuous learning and adaptation
- Digital transformation readiness
- Sustainability implementation
- Talent management and retention

The businesses thriving in Q4 2025 are those that embrace change and invest in future-ready infrastructure.''',
                'excerpt': 'Explore the key market trends shaping Q4 2025 and learn how to position your business for success.',
                'featured': True,
                'published_at': datetime(2025, 11, 10, 9, 15, 0),
            }
        ]
        
        with engine.connect() as conn:
            for post in posts_data:
                # Use raw SQL insert to avoid schema mismatches
                insert_sql = text("""
                    INSERT INTO posts (title, slug, content, excerpt, featured, published_at, created_at, updated_at, locale)
                    VALUES (:title, :slug, :content, :excerpt, :featured, :published_at, :created_at, :updated_at, 'en')
                    ON CONFLICT DO NOTHING
                """)
                
                conn.execute(insert_sql, {
                    'title': post['title'],
                    'slug': post['slug'],
                    'content': post['content'],
                    'excerpt': post['excerpt'],
                    'featured': post['featured'],
                    'published_at': post['published_at'],
                    'created_at': datetime.utcnow(),
                    'updated_at': datetime.utcnow(),
                })
            
            conn.commit()
        
        # Verify insertion
        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) FROM posts"))
            total_posts = result.scalar()
            print(f"\n✅ Sample data populated!")
            print(f"   Total posts in database: {total_posts}")
            
            # Show sample post
            result = conn.execute(text("SELECT title, slug FROM posts LIMIT 1"))
            row = result.fetchone()
            if row:
                print(f"   Sample post: '{row[0]}'")
        
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        session.close()

if __name__ == '__main__':
    populate_data()
    print("\n✨ Done!")
