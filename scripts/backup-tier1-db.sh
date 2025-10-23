#!/bin/bash

# Backup Tier 1 Database to Local/Remote Storage
# Schedule: Weekly (e.g., 0 0 * * 0 /path/to/backup-tier1-db.sh)
# 
# Backups location:
# - Local: backups/tier1/
# - Remote: AWS S3 (optional)
# - GitHub: Git backup (simple text data)

set -e

BACKUP_DIR="backups/tier1"
DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/glad_labs_tier1_$DATE.sql.gz"

# Create backup directory
mkdir -p "$BACKUP_DIR"

echo "💾 Starting Tier 1 Database Backup"
echo "═══════════════════════════════════"
echo "Time: $(date)"
echo "Destination: $BACKUP_FILE"
echo ""

# Step 1: Create backup using Railway
echo "📦 Creating backup via Railway..."

# Get database URL from Railway
# Note: Configure these as environment variables
if [ -z "$DATABASE_URL" ]; then
  echo "❌ ERROR: DATABASE_URL not set"
  echo "   Set environment variable: export DATABASE_URL=postgresql://..."
  exit 1
fi

# Using pg_dump if PostgreSQL client installed
if command -v pg_dump &> /dev/null; then
  echo "  Using pg_dump..."
  pg_dump "$DATABASE_URL" | gzip > "$BACKUP_FILE"
  echo "  ✅ Local backup created"
else
  echo "  ⚠️  pg_dump not found, using Railway backup API..."
  # Use Railway API (requires authentication)
  railway backup create --service postgresql
  echo "  ✅ Railway backup created (use Railway dashboard to download)"
fi

# Step 2: Check file size
if [ -f "$BACKUP_FILE" ]; then
  SIZE=$(du -h "$BACKUP_FILE" | cut -f1)
  echo ""
  echo "📊 Backup Statistics:"
  echo "   File: $BACKUP_FILE"
  echo "   Size: $SIZE"
  echo "   Date: $(date '+%Y-%m-%d %H:%M:%S')"
fi

# Step 3: Optional - Upload to S3
echo ""
echo "☁️  Optional: Upload to AWS S3"

if command -v aws &> /dev/null; then
  read -p "Upload to S3? (y/n) " -n 1 -r
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    S3_BUCKET="${S3_BACKUP_BUCKET:-glad-labs-backups}"
    S3_KEY="tier1/$DATE/backup.sql.gz"
    
    echo "  Uploading to s3://$S3_BUCKET/$S3_KEY..."
    aws s3 cp "$BACKUP_FILE" "s3://$S3_BUCKET/$S3_KEY"
    echo "  ✅ Uploaded to S3"
  fi
else
  echo "  ℹ️  AWS CLI not installed (optional)"
fi

# Step 4: Optional - Commit to Git (for small databases)
echo ""
echo "📝 Optional: Commit to Git (for tracking)"

if [ -d ".git" ]; then
  read -p "Commit backup to Git? (y/n) " -n 1 -r
  echo
  if [[ $REPLY =~ ^[Yy]$ ]]; then
    # Only commit if backup < 50MB to avoid bloating repo
    MAX_SIZE=52428800  # 50 MB
    FILE_SIZE=$(stat -f%z "$BACKUP_FILE" 2>/dev/null || stat -c%s "$BACKUP_FILE")
    
    if [ "$FILE_SIZE" -lt "$MAX_SIZE" ]; then
      git add "$BACKUP_FILE"
      git commit -m "chore: database backup $DATE"
      git push origin main
      echo "  ✅ Committed to Git"
    else
      echo "  ⚠️  Backup too large (>50MB) for Git storage"
      echo "     Use S3 or Railway backup instead"
    fi
  fi
fi

# Step 5: Cleanup old backups (keep last 4 weeks)
echo ""
echo "🧹 Cleaning up old backups..."

KEEP_DAYS=28
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +$KEEP_DAYS -delete

echo "  ✅ Old backups (>$KEEP_DAYS days) removed"

# Step 6: List recent backups
echo ""
echo "📋 Recent Backups:"
ls -lh "$BACKUP_DIR" | tail -5

# Summary
echo ""
echo "═══════════════════════════════════"
echo "✅ Backup Complete!"
echo ""
echo "📌 Backup Storage Options:"
echo "   1. Local: $BACKUP_DIR (current)"
echo "   2. AWS S3: ${S3_BACKUP_BUCKET:-s3://glad-labs-backups}"
echo "   3. Railway: Dashboard > Backups"
echo "   4. Git: Repository (small files only)"
echo ""
echo "💡 Tips:"
echo "   - Schedule weekly: 0 0 * * 0 /path/to/backup-tier1-db.sh"
echo "   - Store in multiple locations"
echo "   - Test restore monthly"
echo "   - Keep 4+ weeks of backups"
echo ""
