# Docker Deployment Guide

## Overview

This guide covers deploying GLAD LABS using Docker and Docker Compose for development, staging, and production environments.

## Prerequisites

- **Docker**: Version 24.0 or higher
- **Docker Compose**: Version 2.20 or higher
- **Environment Variables**: Properly configured `.env` file

## Quick Start

### 1. Development Mode (Default)

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f

# Stop all services
docker-compose down
```

### 2. Production Mode (with PostgreSQL and Redis)

```bash
# Start all services including production databases
docker-compose --profile production up -d

# View logs
docker-compose --profile production logs -f

# Stop all services
docker-compose --profile production down
```

## Environment Configuration

Create a `.env` file in the project root:

```env
# Node Environment
NODE_ENV=production

# Service Ports
STRAPI_PORT=1337
PUBLIC_SITE_PORT=3000
OVERSIGHT_HUB_PORT=3001
COFOUNDER_AGENT_PORT=8000

# Strapi Configuration
STRAPI_JWT_SECRET=your-jwt-secret-here
STRAPI_ADMIN_JWT_SECRET=your-admin-jwt-secret-here
STRAPI_APP_KEYS=your-app-keys-here
STRAPI_API_TOKEN_SALT=your-api-token-salt-here
STRAPI_API_TOKEN=your-api-token-here

# Database (Production)
DATABASE_CLIENT=postgres
POSTGRES_DB=gladlabs
POSTGRES_USER=gladlabs
POSTGRES_PASSWORD=your-secure-password-here
POSTGRES_PORT=5432

# Redis (Production)
REDIS_PASSWORD=your-redis-password-here
REDIS_PORT=6379

# Next.js Public Site
NEXT_PUBLIC_STRAPI_API_URL=http://strapi:1337
NEXT_PUBLIC_SITE_URL=https://gladlabs.ai

# React Oversight Hub
REACT_APP_API_URL=http://localhost:8000
REACT_APP_STRAPI_URL=http://localhost:1337

# AI Co-Founder Agent
ANTHROPIC_API_KEY=your-anthropic-key-here
OPENAI_API_KEY=your-openai-key-here
GOOGLE_API_KEY=your-google-key-here

# Google Cloud Platform
GCP_PROJECT_ID=your-gcp-project-id
GOOGLE_APPLICATION_CREDENTIALS=/app/credentials/gcp-key.json

# Firebase
FIREBASE_PROJECT_ID=your-firebase-project-id
FIREBASE_PRIVATE_KEY=your-firebase-private-key
FIREBASE_CLIENT_EMAIL=your-firebase-client-email

# Security
JWT_SECRET=your-jwt-secret-here
ALLOWED_ORIGINS=https://gladlabs.ai,https://hub.gladlabs.ai

# Rate Limiting
RATE_LIMIT_ENABLED=true
RATE_LIMIT_PER_MINUTE=20

# Logging
LOG_LEVEL=INFO
```

## Service Architecture

### Services

1. **Strapi CMS** (`strapi`)
   - Port: 1337
   - Database: SQLite (dev) or PostgreSQL (prod)
   - Health check: `/_health`

2. **Next.js Public Site** (`public-site`)
   - Port: 3000
   - Depends on: Strapi
   - Health check: `/api/health`

3. **React Oversight Hub** (`oversight-hub`)
   - Port: 3001 (mapped to 80 in container)
   - Depends on: Strapi, Co-Founder Agent
   - Health check: `/health`

4. **AI Co-Founder Agent** (`cofounder-agent`)
   - Port: 8000
   - Python FastAPI service
   - Health check: `/metrics/health`

5. **PostgreSQL** (`postgres`) - Production only
   - Port: 5432
   - Profile: production

6. **Redis** (`redis`) - Production only
   - Port: 6379
   - Profile: production

### Networks

- **glad-labs-network**: Bridge network connecting all services

### Volumes

- `glad-labs-strapi-data`: Strapi database files
- `glad-labs-strapi-uploads`: Strapi media uploads
- `glad-labs-cofounder-logs`: Co-Founder Agent logs
- `glad-labs-cofounder-cache`: Co-Founder Agent cache
- `glad-labs-postgres-data`: PostgreSQL data (production)
- `glad-labs-redis-data`: Redis data (production)

## Building Images

### Build All Images

```bash
docker-compose build
```

### Build Specific Service

```bash
docker-compose build strapi
docker-compose build public-site
docker-compose build oversight-hub
docker-compose build cofounder-agent
```

### Build with No Cache

```bash
docker-compose build --no-cache
```

## Managing Services

### Start Services

```bash
# Start all services in background
docker-compose up -d

# Start specific service
docker-compose up -d strapi

# Start with rebuild
docker-compose up -d --build
```

### Stop Services

```bash
# Stop all services
docker-compose stop

# Stop specific service
docker-compose stop strapi

# Stop and remove containers
docker-compose down

# Stop and remove containers + volumes
docker-compose down -v
```

### Restart Services

```bash
# Restart all services
docker-compose restart

# Restart specific service
docker-compose restart strapi
```

## Monitoring and Logs

### View Logs

```bash
# Follow all logs
docker-compose logs -f

# Follow specific service logs
docker-compose logs -f strapi
docker-compose logs -f cofounder-agent

# View last 100 lines
docker-compose logs --tail=100

# View logs since timestamp
docker-compose logs --since 2024-01-01T00:00:00
```

### Check Service Status

```bash
# List running containers
docker-compose ps

# Check service health
docker-compose ps strapi
```

### Execute Commands in Containers

```bash
# Open shell in container
docker-compose exec strapi sh
docker-compose exec cofounder-agent bash

# Run one-off command
docker-compose exec strapi npm run strapi
docker-compose exec cofounder-agent python -m pytest
```

## Health Checks

All services include health checks:

```bash
# Check Strapi
curl http://localhost:1337/_health

# Check Public Site
curl http://localhost:3000/api/health

# Check Oversight Hub
curl http://localhost:3001/health

# Check Co-Founder Agent
curl http://localhost:8000/metrics/health
```

## Scaling Services

```bash
# Scale a service to multiple instances
docker-compose up -d --scale cofounder-agent=3

# Note: Requires load balancer configuration
```

## Data Persistence

### Backup Volumes

```bash
# Backup Strapi data
docker run --rm -v glad-labs-strapi-data:/data -v $(pwd):/backup alpine tar czf /backup/strapi-data-backup.tar.gz -C /data .

# Backup PostgreSQL
docker-compose exec postgres pg_dump -U gladlabs gladlabs > backup.sql
```

### Restore Volumes

```bash
# Restore Strapi data
docker run --rm -v glad-labs-strapi-data:/data -v $(pwd):/backup alpine tar xzf /backup/strapi-data-backup.tar.gz -C /data

# Restore PostgreSQL
docker-compose exec -T postgres psql -U gladlabs gladlabs < backup.sql
```

## Production Deployment

### 1. Update Environment Variables

Ensure all production values are set in `.env` file.

### 2. Build Production Images

```bash
docker-compose --profile production build
```

### 3. Start Services

```bash
docker-compose --profile production up -d
```

### 4. Verify Health

```bash
# Check all services are healthy
docker-compose ps

# Test endpoints
curl http://localhost:1337/_health
curl http://localhost:3000/api/health
curl http://localhost:3001/health
curl http://localhost:8000/metrics/health
```

### 5. Monitor Logs

```bash
docker-compose logs -f
```

## Troubleshooting

### Service Won't Start

```bash
# Check logs
docker-compose logs [service-name]

# Check configuration
docker-compose config

# Verify environment variables
docker-compose exec [service-name] env
```

### Port Already in Use

```bash
# Find process using port
netstat -ano | findstr :1337

# Change port in .env file
STRAPI_PORT=1338
```

### Permission Denied Errors

```bash
# Fix volume permissions
docker-compose exec strapi chown -R node:node /app/.tmp
docker-compose exec cofounder-agent chown -R cofounder:cofounder /app/logs
```

### Out of Memory

```bash
# Increase Docker memory limit in Docker Desktop settings
# Or use docker-compose memory limits

services:
  strapi:
    deploy:
      resources:
        limits:
          memory: 2G
```

### Network Issues

```bash
# Recreate network
docker-compose down
docker network rm glad-labs-network
docker-compose up -d

# Inspect network
docker network inspect glad-labs-network
```

## CI/CD Integration

### GitHub Actions Example

```yaml
name: Deploy to Production

on:
  push:
    branches: [main]

jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Build and Push Images
        run: |
          docker-compose build
          docker-compose push

      - name: Deploy to Server
        run: |
          ssh user@server "cd /app && docker-compose pull && docker-compose up -d"
```

## Security Best Practices

1. **Never commit `.env` files** to version control
2. **Use secrets management** for production credentials
3. **Run containers as non-root** users (already configured)
4. **Limit container resources** to prevent DoS
5. **Use Docker secrets** for sensitive data in Swarm mode
6. **Regularly update base images** for security patches
7. **Enable security scanning** with `docker scan`

## Performance Optimization

1. **Multi-stage builds**: Reduces image size (already implemented)
2. **Layer caching**: Order Dockerfile commands for optimal caching
3. **Minimize dependencies**: Only install required packages
4. **Use alpine images**: Smaller footprint when possible
5. **Health checks**: Enable automatic container restart on failure
6. **Resource limits**: Prevent resource exhaustion

## Next Steps

1. Set up **container registry** (Docker Hub, AWS ECR, Azure ACR)
2. Configure **reverse proxy** (nginx, Traefik) for production
3. Implement **SSL/TLS** certificates with Let's Encrypt
4. Set up **monitoring** (Prometheus, Grafana)
5. Configure **log aggregation** (ELK Stack, Splunk)
6. Implement **automated backups**
7. Set up **CI/CD pipeline** for automated deployments

## Support

For issues or questions:

- Check service logs: `docker-compose logs -f [service-name]`
- Review health checks: `docker-compose ps`
- Consult documentation: `docs/`
- Contact DevOps team
