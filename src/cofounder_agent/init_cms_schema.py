"""
FastAPI CMS Database Schema Setup
Creates all necessary tables for content management in PostgreSQL
Run this once to initialize the database
"""

import os
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime

DATABASE_URL = os.getenv('DATABASE_URL', 'postgresql://postgres:postgres@localhost:5432/glad_labs_dev')

def normalize_db_url(url):
    """Remove async prefix if present"""
    return url.replace('postgresql+asyncpg://', 'postgresql://')

def create_schema():
    """Create all CMS tables"""
    conn = psycopg2.connect(normalize_db_url(DATABASE_URL))
    cur = conn.cursor()
    
    try:
        # Enable UUID extension
        cur.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp"')
        
        # Authors table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS authors (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                name VARCHAR(255) NOT NULL,
                slug VARCHAR(255) NOT NULL UNIQUE,
                email VARCHAR(255),
                bio TEXT,
                avatar_url VARCHAR(500),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("✓ Authors table created")
        
        # Categories table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS categories (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                name VARCHAR(255) NOT NULL UNIQUE,
                slug VARCHAR(255) NOT NULL UNIQUE,
                description TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("✓ Categories table created")
        
        # Tags table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS tags (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                name VARCHAR(255) NOT NULL UNIQUE,
                slug VARCHAR(255) NOT NULL UNIQUE,
                description TEXT,
                color VARCHAR(7),
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        print("✓ Tags table created")
        
        # Posts table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS posts (
                id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
                title VARCHAR(500) NOT NULL,
                slug VARCHAR(500) NOT NULL UNIQUE,
                content TEXT NOT NULL,
                excerpt VARCHAR(1000),
                featured_image_url VARCHAR(500),
                cover_image_url VARCHAR(500),
                author_id UUID REFERENCES authors(id) ON DELETE CASCADE,
                category_id UUID REFERENCES categories(id) ON DELETE SET NULL,
                tag_ids UUID[],
                seo_title VARCHAR(255),
                seo_description VARCHAR(500),
                seo_keywords VARCHAR(500),
                status VARCHAR(50) DEFAULT 'draft',
                published_at TIMESTAMP,
                view_count INTEGER DEFAULT 0,
                metadata JSONB DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_by UUID,
                updated_by UUID
            )
        ''')
        print("✓ Posts table created")
        
        # Create index for faster queries
        cur.execute('CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_posts_category_id ON posts(category_id)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_posts_published_at ON posts(published_at DESC)')
        cur.execute('CREATE INDEX IF NOT EXISTS idx_posts_slug ON posts(slug)')
        print("✓ Posts indexes created")
        
        # Post-Tags junction table
        cur.execute('''
            CREATE TABLE IF NOT EXISTS post_tags (
                post_id UUID REFERENCES posts(id) ON DELETE CASCADE,
                tag_id UUID REFERENCES tags(id) ON DELETE CASCADE,
                PRIMARY KEY (post_id, tag_id)
            )
        ''')
        print("✓ Post-Tags junction table created")
        
        conn.commit()
        print("\n✅ All CMS tables created successfully!")
        
    except Exception as e:
        conn.rollback()
        print(f"❌ Error creating schema: {e}")
        raise
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    print("Creating FastAPI CMS Schema...")
    create_schema()
