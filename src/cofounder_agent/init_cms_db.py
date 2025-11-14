"""
Initialize fresh CMS database schema for Glad Labs.

Creates tables: posts, categories, tags, post_tags

Run with:
    python init_cms_db.py
"""

import os
from datetime import datetime
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

# Load environment
load_dotenv()

def init_database():
    """Create CMS tables in clean database."""
    
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/glad_labs_dev')
    
    # Convert async to sync if needed
    if '+asyncpg' in database_url:
        database_url = database_url.replace('postgresql+asyncpg://', 'postgresql://')
    
    print(f"🔌 Connecting to: {database_url.split('@')[-1]}")
    
    engine = create_engine(database_url, echo=False)
    
    with engine.connect() as conn:
        print("📝 Creating tables...")
        
        # Categories table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                slug VARCHAR(255) UNIQUE NOT NULL,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        print("   ✅ categories")
        
        # Tags table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS tags (
                id SERIAL PRIMARY KEY,
                name VARCHAR(255) NOT NULL,
                slug VARCHAR(255) UNIQUE NOT NULL,
                description TEXT,
                color VARCHAR(7),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        print("   ✅ tags")
        
        # Posts table
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS posts (
                id SERIAL PRIMARY KEY,
                title VARCHAR(500) NOT NULL,
                slug VARCHAR(500) UNIQUE NOT NULL,
                content TEXT NOT NULL,
                excerpt VARCHAR(1000),
                featured BOOLEAN DEFAULT FALSE,
                category_id INTEGER REFERENCES categories(id) ON DELETE SET NULL,
                published_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                seo_title VARCHAR(255),
                seo_description VARCHAR(500),
                seo_keywords VARCHAR(255)
            )
        """))
        print("   ✅ posts")
        
        # Post-Tags many-to-many
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS post_tags (
                post_id INTEGER REFERENCES posts(id) ON DELETE CASCADE,
                tag_id INTEGER REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (post_id, tag_id)
            )
        """))
        print("   ✅ post_tags")
        
        # Create indexes
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_posts_slug ON posts(slug)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_posts_published_at ON posts(published_at DESC)"))
        conn.execute(text("CREATE INDEX IF NOT EXISTS idx_posts_category_id ON posts(category_id)"))
        print("   ✅ indexes")
        
        conn.commit()
    
    print("\n✨ Database schema created!")
    return True

if __name__ == '__main__':
    try:
        init_database()
        print("\n✅ CMS database ready!")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
