#!/bin/bash
# Railway Project Setup Script for GLAD Labs Strapi Backend
# This script sets up a new Railway project with Strapi and PostgreSQL

echo "🚀 Setting up GLAD Labs Strapi Backend on Railway CLI..."
echo ""

# Step 1: Check if Railway CLI is installed
echo "📦 Checking Railway CLI installation..."
if ! command -v railway &> /dev/null; then
    echo "❌ Railway CLI not found. Installing..."
    npm i -g @railway/cli
else
    echo "✅ Railway CLI found"
fi

echo ""
echo "🔐 Authenticating with Railway..."
railway login

echo ""
echo "📁 Initializing new Railway project..."
railway init --name glad-labs-strapi

echo ""
echo "✅ Project initialized!"
echo ""
echo "Next steps:"
echo "1. Add PostgreSQL plugin:"
echo "   railway add --plugin postgres"
echo ""
echo "2. Set environment variables:"
echo "   railway variables set DATABASE_CLIENT=postgres"
echo "   railway variables set HOST=0.0.0.0"
echo "   railway variables set PORT=1337"
echo "   railway variables set STRAPI_TELEMETRY_DISABLED=true"
echo ""
echo "3. Deploy Strapi:"
echo "   railway deploy"
echo ""
echo "4. View logs:"
echo "   railway logs"
echo ""
echo "5. Open admin panel:"
echo "   railway open"
