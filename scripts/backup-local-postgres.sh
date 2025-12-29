#!/bin/bash

# 🗄️  Local PostgreSQL Database Backup Script
# Backs up your local glad_labs_dev database to local storage
# 
# Usage:
#   bash backup-local-postgres.sh                    # Uses default settings
#   bash backup-local-postgres.sh glad_labs_dev      # Specify database name
#   bash backup-local-postgres.sh --help             # Show options
#
# Schedule (crontab):
#   # Daily at 2 AM
#   0 2 * * * /home/user/glad-labs-website/scripts/backup-local-postgres.sh
#
#   # Weekly on Sunday at midnight
#   0 0 * * 0 /home/user/glad-labs-website/scripts/backup-local-postgres.sh

set -e

# ═══════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════

# Database connection settings
DB_HOST="${DB_HOST:-localhost}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${1:-glad_labs_dev}"

# Backup storage
BACKUP_DIR="backups/postgres"
DATE=$(date +%Y%m%d_%H%M%S)
DATE_PRETTY=$(date '+%Y-%m-%d %H:%M:%S')
BACKUP_FILE="$BACKUP_DIR/${DB_NAME}_${DATE}.sql"
BACKUP_FILE_GZ="${BACKUP_FILE}.gz"

# Options
COMPRESS=true
KEEP_DAYS=30
VERBOSE=true

# ═══════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ═══════════════════════════════════════════════════════════

show_help() {
  cat << EOF
🗄️  Local PostgreSQL Database Backup Script

USAGE:
  bash backup-local-postgres.sh [options] [database_name]

OPTIONS:
  --help              Show this help message
  --no-compress       Don't compress backup (creates .sql instead of .sql.gz)
  --keep-days N       Keep backups for N days (default: 30)
  --host HOST         Database host (default: localhost)
  --port PORT         Database port (default: 5432)
  --user USER         Database user (default: postgres)

EXAMPLES:
  # Backup default database (glad_labs_dev)
  bash backup-local-postgres.sh

  # Backup specific database
  bash backup-local-postgres.sh my_database

  # Backup without compression
  bash backup-local-postgres.sh --no-compress glad_labs_dev

  # Custom connection settings
  bash backup-local-postgres.sh --host 192.168.1.100 --port 5433 mydb

ENVIRONMENT VARIABLES:
  DB_HOST              Database host (default: localhost)
  DB_PORT              Database port (default: 5432)
  DB_USER              Database user (default: postgres)
  PGPASSWORD           Password (if needed - not recommended)

SCHEDULING:
  # Add to crontab for automatic daily backups at 2 AM
  crontab -e

  0 2 * * * cd /path/to/glad-labs-website && bash scripts/backup-local-postgres.sh

EOF
}

log() {
  if [ "$VERBOSE" = true ]; then
    echo "[$(date '+%H:%M:%S')] $1"
  fi
}

log_error() {
  echo "❌ ERROR: $1" >&2
}

log_success() {
  echo "✅ $1"
}

# ═══════════════════════════════════════════════════════════
# PARSE ARGUMENTS
# ═══════════════════════════════════════════════════════════

while [[ $# -gt 0 ]]; do
  case $1 in
    --help)
      show_help
      exit 0
      ;;
    --no-compress)
      COMPRESS=false
      shift
      ;;
    --keep-days)
      KEEP_DAYS="$2"
      shift 2
      ;;
    --host)
      DB_HOST="$2"
      shift 2
      ;;
    --port)
      DB_PORT="$2"
      shift 2
      ;;
    --user)
      DB_USER="$2"
      shift 2
      ;;
    -*)
      log_error "Unknown option: $1"
      show_help
      exit 1
      ;;
    *)
      DB_NAME="$1"
      shift
      ;;
  esac
done

# ═══════════════════════════════════════════════════════════
# VALIDATION
# ═══════════════════════════════════════════════════════════

# Check if pg_dump is installed
if ! command -v pg_dump &> /dev/null; then
  log_error "pg_dump not found!"
  echo ""
  echo "PostgreSQL client tools are required."
  echo ""
  echo "Install on Linux/Mac:"
  echo "  Ubuntu/Debian: sudo apt-get install postgresql-client"
  echo "  macOS: brew install postgresql"
  echo ""
  echo "Install on Windows:"
  echo "  1. Download PostgreSQL installer: https://www.postgresql.org/download/windows/"
  echo "  2. During installation, include 'Command Line Tools'"
  echo "  3. Or use: choco install postgresql --params '/InstallComponents=tools'"
  echo ""
  exit 1
fi

# Test database connection
log "Testing connection to $DB_HOST:$DB_PORT as $DB_USER..."
if ! PGPASSWORD="${PGPASSWORD:-}" psql -h "$DB_HOST" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -c "SELECT 1" > /dev/null 2>&1; then
  log_error "Cannot connect to database!"
  echo ""
  echo "Connection Details:"
  echo "  Host: $DB_HOST"
  echo "  Port: $DB_PORT"
  echo "  User: $DB_USER"
  echo "  Database: $DB_NAME"
  echo ""
  echo "Solutions:"
  echo "  1. Check PostgreSQL is running"
  echo "  2. Verify connection parameters (--host, --port, --user)"
  echo "  3. Set password: export PGPASSWORD=your_password"
  echo "  4. Check PostgreSQL user has permissions"
  echo ""
  exit 1
fi

log_success "Database connection successful"

# Create backup directory
mkdir -p "$BACKUP_DIR"
log "Backup directory: $BACKUP_DIR"

# ═══════════════════════════════════════════════════════════
# PERFORM BACKUP
# ═══════════════════════════════════════════════════════════

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "🗄️  PostgreSQL Database Backup"
echo "═══════════════════════════════════════════════════════════"
echo ""
echo "📊 Backup Information:"
echo "   Database:  $DB_NAME"
echo "   Host:      $DB_HOST:$DB_PORT"
echo "   User:      $DB_USER"
echo "   Time:      $DATE_PRETTY"
echo "   Location:  $BACKUP_DIR"
echo ""
echo "⏳ Backing up database..."
echo ""

# Create backup
if [ "$COMPRESS" = true ]; then
  PGPASSWORD="${PGPASSWORD:-}" pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --no-password \
    --verbose \
    | gzip > "$BACKUP_FILE_GZ" 2>&1 | tail -20

  FINAL_FILE="$BACKUP_FILE_GZ"
else
  PGPASSWORD="${PGPASSWORD:-}" pg_dump \
    -h "$DB_HOST" \
    -p "$DB_PORT" \
    -U "$DB_USER" \
    -d "$DB_NAME" \
    --no-password \
    --verbose \
    > "$BACKUP_FILE" 2>&1 | tail -20

  FINAL_FILE="$BACKUP_FILE"
fi

# Check if backup was successful
if [ ! -f "$FINAL_FILE" ]; then
  log_error "Backup file not created!"
  exit 1
fi

# Get file size
FILE_SIZE=$(du -h "$FINAL_FILE" | cut -f1)

echo ""
echo "═══════════════════════════════════════════════════════════"
log_success "Backup completed successfully!"
echo ""
echo "📋 Backup Details:"
echo "   File:      $(basename "$FINAL_FILE")"
echo "   Size:      $FILE_SIZE"
echo "   Location:  $(pwd)/$FINAL_FILE"
echo "   Date:      $DATE_PRETTY"
echo ""

# ═══════════════════════════════════════════════════════════
# CLEANUP OLD BACKUPS
# ═══════════════════════════════════════════════════════════

echo "🧹 Cleaning up old backups (keeping last $KEEP_DAYS days)..."

# Count files before cleanup
BEFORE=$(find "$BACKUP_DIR" -name "${DB_NAME}_*.sql*" -type f | wc -l)

# Remove old backups
find "$BACKUP_DIR" -name "${DB_NAME}_*.sql*" -type f -mtime +$KEEP_DAYS -delete

# Count files after cleanup
AFTER=$(find "$BACKUP_DIR" -name "${DB_NAME}_*.sql*" -type f | wc -l)
REMOVED=$((BEFORE - AFTER))

if [ "$REMOVED" -gt 0 ]; then
  log_success "Removed $REMOVED old backup(s)"
else
  log "No old backups to remove"
fi

# ═══════════════════════════════════════════════════════════
# SUMMARY
# ═══════════════════════════════════════════════════════════

echo ""
echo "📋 Recent Backups:"
ls -lh "$BACKUP_DIR"/${DB_NAME}_*.sql* 2>/dev/null | tail -5 || echo "   No backups found"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "✅ All Done!"
echo ""
echo "💡 Restore Command:"
echo "   gunzip < $FINAL_FILE | psql -h $DB_HOST -U $DB_USER -d ${DB_NAME}_restored"
echo ""
echo "💡 Schedule Backups (crontab):"
echo "   0 2 * * * cd $(pwd) && bash scripts/backup-local-postgres.sh"
echo ""
