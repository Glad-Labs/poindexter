#!/bin/bash
ENV="${1:-staging}"

echo "Deploying to ${ENV}..."
railway environment "$ENV" 2>&1
railway up --detach 2>&1

echo ""
echo "Deployment triggered. Check status with 'railway status'"
