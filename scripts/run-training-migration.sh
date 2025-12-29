#!/bin/bash
# Training Data Migration Runner
# Runs the training data migration to create all required tables

set -e

echo "🚀 Training Data Migration Runner"
echo "=================================="

# Get database credentials from environment
DATABASE_USER="${DATABASE_USER:-postgres}"
DATABASE_PASSWORD="${DATABASE_PASSWORD:-}"
DATABASE_HOST="${DATABASE_HOST:-localhost}"
DATABASE_PORT="${DATABASE_PORT:-5432}"
DATABASE_NAME="${DATABASE_NAME:-glad_labs}"

# Build connection string
if [ -z "$DATABASE_PASSWORD" ]; then
    CONN_STRING="postgresql://$DATABASE_USER@$DATABASE_HOST:$DATABASE_PORT/$DATABASE_NAME"
    echo "📍 Using connection string (no password): postgresql://$DATABASE_USER@$DATABASE_HOST:$DATABASE_PORT/$DATABASE_NAME"
else
    CONN_STRING="postgresql://$DATABASE_USER:***@$DATABASE_HOST:$DATABASE_PORT/$DATABASE_NAME"
    echo "📍 Using connection string with password"
fi

# Alternative: use environment variable if set
if [ ! -z "$DATABASE_URL" ]; then
    echo "📍 Using DATABASE_URL environment variable"
    CONN_STRING="$DATABASE_URL"
fi

# Check if psql is available
if ! command -v psql &> /dev/null; then
    echo "❌ ERROR: psql not found. Please install PostgreSQL client tools."
    echo "   On Windows: Install PostgreSQL or use WSL with: apt-get install postgresql-client"
    echo "   On macOS: brew install postgresql"
    echo "   On Linux: apt-get install postgresql-client"
    exit 1
fi

echo ""
echo "📌 Migration Details:"
echo "   File: src/cofounder_agent/migrations/003_training_data_tables.sql"
echo "   Tables: 8 (orchestrator_training_data, training_datasets, fine_tuning_jobs, etc)"
echo "   Status: About to execute..."
echo ""

# Run the migration
echo "⏳ Executing migration..."

MIGRATION_FILE="src/cofounder_agent/migrations/003_training_data_tables.sql"

if [ ! -f "$MIGRATION_FILE" ]; then
    echo "❌ ERROR: Migration file not found: $MIGRATION_FILE"
    exit 1
fi

# Run psql with the migration file
if [ -z "$DATABASE_PASSWORD" ]; then
    psql -U "$DATABASE_USER" -h "$DATABASE_HOST" -p "$DATABASE_PORT" -d "$DATABASE_NAME" -f "$MIGRATION_FILE"
else
    PGPASSWORD="$DATABASE_PASSWORD" psql -U "$DATABASE_USER" -h "$DATABASE_HOST" -p "$DATABASE_PORT" -d "$DATABASE_NAME" -f "$MIGRATION_FILE"
fi

MIGRATION_STATUS=$?

if [ $MIGRATION_STATUS -eq 0 ]; then
    echo ""
    echo "✅ Migration completed successfully!"
    echo ""
    
    # Verify tables were created
    echo "📊 Verifying tables..."
    
    if [ -z "$DATABASE_PASSWORD" ]; then
        TABLES=$(psql -U "$DATABASE_USER" -h "$DATABASE_HOST" -p "$DATABASE_PORT" -d "$DATABASE_NAME" -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND table_name LIKE 'orchestrator_%' OR table_name LIKE 'training_%' OR table_name LIKE 'fine_tuning_%' OR table_name LIKE 'learning_%' OR table_name LIKE 'social_%' OR table_name LIKE 'web_analytics' OR table_name LIKE 'financial_%';")
    else
        TABLES=$(PGPASSWORD="$DATABASE_PASSWORD" psql -U "$DATABASE_USER" -h "$DATABASE_HOST" -p "$DATABASE_PORT" -d "$DATABASE_NAME" -t -c "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='public' AND (table_name LIKE 'orchestrator_%' OR table_name LIKE 'training_%' OR table_name LIKE 'fine_tuning_%' OR table_name LIKE 'learning_%' OR table_name LIKE 'social_%' OR table_name LIKE 'web_analytics' OR table_name LIKE 'financial_%');")
    fi
    
    echo "   Found training-related tables: $TABLES"
    echo ""
    echo "🎉 Training system database is ready!"
    echo ""
    echo "Next steps:"
    echo "  1. Configure environment variables (see QUICK_INTEGRATION_GUIDE.md Step 3)"
    echo "  2. Start the backend: npm start"
    echo "  3. Test API endpoints: curl http://localhost:8000/api/orchestrator/training/stats"
    echo ""
else
    echo ""
    echo "❌ Migration failed with status $MIGRATION_STATUS"
    echo ""
    echo "Troubleshooting:"
    echo "  - Check database credentials in environment variables"
    echo "  - Ensure PostgreSQL is running"
    echo "  - Try running psql manually to test connection:"
    echo "    psql -U $DATABASE_USER -h $DATABASE_HOST -d $DATABASE_NAME"
    exit 1
fi
