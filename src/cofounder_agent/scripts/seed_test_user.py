#!/usr/bin/env python3
"""
Seed database with test user for development/testing.

This script creates a test user account that can be used for E2E testing:
- Email: test@example.com
- Password: TestPassword123!
- Status: Active

Usage:
    python scripts/seed_test_user.py
    
    Or from src/cofounder_agent directory:
    python -c "from scripts.seed_test_user import seed_test_user; seed_test_user()"
"""

import os
import sys
from datetime import datetime, timezone
import uuid as uuid_lib

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from models import Base, User
from services.encryption import get_encryption_service


def seed_test_user():
    """Create test user in database."""
    
    # Get database URL from environment or use default
    database_url = os.getenv("DATABASE_URL", "sqlite:///./test.db")
    
    print(f"📦 Connecting to database: {database_url}")
    
    try:
        # Create engine and session
        engine = create_engine(database_url)
        SessionLocal = sessionmaker(bind=engine)
        db: Session = SessionLocal()
        
        # Create all tables
        print("📋 Creating tables...")
        Base.metadata.create_all(bind=engine)
        print("✅ Tables created/verified")
        
        # Check if test user already exists
        existing_user = db.query(User).filter_by(email="test@example.com").first()
        if existing_user:
            print("⚠️ Test user already exists (test@example.com)")
            print(f"   - Username: {existing_user.username}")
            print(f"   - Active: {existing_user.is_active}")
            print(f"   - Created: {existing_user.created_at}")
            db.close()
            return
        
        # Create test user
        print("👤 Creating test user...")
        encryption = get_encryption_service()
        password_hash, password_salt = encryption.hash_password("TestPassword123!")
        
        test_user = User(
            id=uuid_lib.uuid4(),
            username="testuser",
            email="test@example.com",
            password_hash=password_hash,
            password_salt=password_salt,
            first_name="Test",
            last_name="User",
            is_active=True,
            is_locked=False,
            failed_login_attempts=0,
            totp_enabled=False,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        
        db.add(test_user)
        db.commit()
        
        print("✅ Test user created successfully!")
        print(f"   - Email: test@example.com")
        print(f"   - Password: TestPassword123!")
        print(f"   - Username: testuser")
        print(f"   - Status: Active")
        print(f"   - User ID: {test_user.id}")
        print()
        print("🎉 You can now login with these credentials in the web interface")
        
        db.close()
        
    except Exception as e:
        print(f"❌ Error seeding database: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    seed_test_user()
