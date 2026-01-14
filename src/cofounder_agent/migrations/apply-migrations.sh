#!/bin/bash
# Script to apply database migrations to production PostgreSQL

set -e

# Check if DATABASE_URL is set
if [ -z "$DATABASE_URL" ]; then
    echo "❌ DATABASE_URL environment variable not set"
    echo "Please set DATABASE_URL to your PostgreSQL connection string"
    exit 1
fi

echo "🔄 Applying database migrations..."
echo "📍 Target: $DATABASE_URL"

# Get the directory where this script is located
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
MIGRATIONS_DIR="$SCRIPT_DIR"

# Find and apply all SQL migration files in order
for migration_file in $(find "$MIGRATIONS_DIR" -name "*.sql" | sort); do
    echo ""
    echo "📝 Applying migration: $(basename $migration_file)"
    
    # Apply the migration using psql
    psql "$DATABASE_URL" < "$migration_file"
    
    if [ $? -eq 0 ]; then
        echo "✅ Migration completed: $(basename $migration_file)"
    else
        echo "❌ Migration failed: $(basename $migration_file)"
        exit 1
    fi
done

echo ""
echo "✅ All database migrations applied successfully!"
