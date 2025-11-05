#!/usr/bin/env bash
# =============================================================================
# Glad Labs PostgreSQL Quick Start - Local Development
# =============================================================================

echo "🚀 Glad Labs Local PostgreSQL Setup"
echo "===================================="
echo ""

# Color codes
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check PostgreSQL
echo -e "${YELLOW}Checking PostgreSQL...${NC}"
if psql --version > /dev/null 2>&1; then
    echo -e "${GREEN}✅ PostgreSQL found$(psql --version | cut -d' ' -f3-)${NC}"
else
    echo -e "${RED}❌ PostgreSQL not installed or not in PATH${NC}"
    exit 1
fi

echo ""

# Check database
echo -e "${YELLOW}Checking glad_labs_dev database...${NC}"
if psql -U postgres -lqt | cut -d \| -f 1 | grep -qw glad_labs_dev; then
    echo -e "${GREEN}✅ Database exists${NC}"
else
    echo -e "${RED}❌ Database not found, creating...${NC}"
    createdb -U postgres glad_labs_dev || {
        echo -e "${RED}Failed to create database${NC}"
        exit 1
    }
    echo -e "${GREEN}✅ Database created${NC}"
fi

echo ""

# Check environment
echo -e "${YELLOW}Checking environment configuration...${NC}"
if grep -q "DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev" .env.local; then
    echo -e "${GREEN}✅ DATABASE_URL configured correctly${NC}"
else
    echo -e "${YELLOW}⚠️  DATABASE_URL may not be set correctly${NC}"
    echo "Update .env.local with:"
    echo "  DATABASE_URL=postgresql://postgres:postgres@localhost:5432/glad_labs_dev"
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✅ PostgreSQL Local Setup Complete!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "Next steps:"
echo "  1. npm run setup:all     # Install dependencies"
echo "  2. npm run dev           # Start all services"
echo ""
echo "Services will start on:"
echo "  • Strapi: http://localhost:1337"
echo "  • Co-Founder Agent: http://localhost:8000"
echo "  • Oversight Hub: http://localhost:3001"
echo "  • Public Site: http://localhost:3000"
echo ""

