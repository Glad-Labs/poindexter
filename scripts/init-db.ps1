# Initialize PostgreSQL database with schema
# Usage: .\scripts\init-db.ps1

param(
    [string]$Host = "localhost",
    [int]$Port = 5432,
    [string]$User = "glad_labs_dev",
    [string]$Password = "Glad3221",
    [string]$Database = "glad_labs_development"
)

Write-Host "🗄️  Initializing PostgreSQL database..." -ForegroundColor Cyan

# Check if psql is available
try {
    $psqlVersion = psql --version
    Write-Host "✅ Found psql: $psqlVersion" -ForegroundColor Green
}
catch {
    Write-Host "❌ Error: psql not found. Please install PostgreSQL." -ForegroundColor Red
    exit 1
}

# Test connection
Write-Host "Testing database connection..."
$env:PGPASSWORD = $Password
try {
    $result = psql -h $Host -p $Port -U $User -d $Database -c "SELECT version();" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "✅ Database connection successful!" -ForegroundColor Green
    }
}
catch {
    Write-Host "❌ Failed to connect to database: $_" -ForegroundColor Red
    exit 1
}

# Run schema migration SQL
Write-Host "Creating database schema..."
$sqlFile = Join-Path $PSScriptRoot ".." "src\cofounder_agent\migrations\versions\001_initial_schema.py"

# Convert Python migration to SQL (simplified approach)
# For now, create basic tables that the application needs
$schemaSQL = @"
-- Create tasks table (new content generation schema)
CREATE TABLE IF NOT EXISTS tasks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_name VARCHAR(255) NOT NULL,
    topic TEXT,
    primary_keyword VARCHAR(255),
    target_audience VARCHAR(255),
    category VARCHAR(100) DEFAULT 'general',
    status VARCHAR(50) DEFAULT 'pending',
    agent_id VARCHAR(255) DEFAULT 'content-agent',
    user_id VARCHAR(255) DEFAULT 'system',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- Create indexes for better query performance
CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_category ON tasks(category);
CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);

-- Create users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(255) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_username ON users(username);

-- Create sessions table
CREATE TABLE IF NOT EXISTS sessions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token VARCHAR(255) UNIQUE NOT NULL,
    expires_at TIMESTAMP NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sessions_user_id ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token);

-- Create settings table
CREATE TABLE IF NOT EXISTS settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    key VARCHAR(255) UNIQUE NOT NULL,
    value JSONB NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_settings_key ON settings(key);

-- Create agent_logs table
CREATE TABLE IF NOT EXISTS agent_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_name VARCHAR(255) NOT NULL,
    task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    level VARCHAR(50) DEFAULT 'INFO',
    message TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_logs_agent_name ON agent_logs(agent_name);
CREATE INDEX IF NOT EXISTS idx_agent_logs_task_id ON agent_logs(task_id);
CREATE INDEX IF NOT EXISTS idx_agent_logs_level ON agent_logs(level);
CREATE INDEX IF NOT EXISTS idx_agent_logs_created_at ON agent_logs(created_at DESC);
"@

# Write SQL to temp file
$tempSql = [System.IO.Path]::GetTempFileName()
$schemaSQL | Out-File -FilePath $tempSql -Encoding UTF8

# Execute SQL
Write-Host "Executing schema SQL..."
psql -h $Host -p $Port -U $User -d $Database -f $tempSql

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ Database schema created successfully!" -ForegroundColor Green
    Remove-Item -Path $tempSql
}
else {
    Write-Host "❌ Failed to create database schema" -ForegroundColor Red
    Write-Host "SQL file: $tempSql (kept for debugging)"
    exit 1
}

Write-Host ""
Write-Host "🎉 Database initialization complete!" -ForegroundColor Green
Write-Host "Database: $Database" -ForegroundColor Cyan
Write-Host "User: $User" -ForegroundColor Cyan
Write-Host "Connection string: postgresql://$User:***@$Host`:$Port/$Database" -ForegroundColor Cyan
