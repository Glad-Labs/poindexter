#!/usr/bin/env node

/**
 * GLAD Labs Tier 1 (Ultra-Budget) Production Deployment Setup
 * 
 * Cost: ~$10-15/month
 * Resources: Minimal shared CPU, limited memory
 * Uptime: ~95% (services may sleep after inactivity)
 * Users: 10-50 concurrent
 * 
 * Usage: node scripts/setup-tier1.js
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

console.log('🚀 GLAD Labs Tier 1 Production Setup');
console.log('═'.repeat(60));

const TIER1_CONFIG = {
  name: 'glad-labs-tier1-prod',
  railway: {
    services: {
      postgres: {
        type: 'postgres',
        plan: 'free', // Railway free tier
        resources: {
          cpu: 'shared',
          memory: '256Mi', // Minimal
          storage: '1Gi', // 1GB - enough for moderate data
        },
        backup: {
          enabled: true,
          frequency: 'weekly', // Less frequent to save costs
          retention: '3d',
        },
      },
      strapi: {
        type: 'node',
        builder: 'dockerfile',
        resources: {
          cpu: 'shared',
          memory: '256Mi',
          disk: '512Mi', // Ephemeral only
        },
        scaling: {
          minReplicas: 1,
          maxReplicas: 1, // No auto-scaling to save costs
          sleepOnInactivity: true,
          sleepTimeout: '30m', // Sleep after 30 min inactivity
        },
        startCommand: 'npm run start',
      },
      cofounder_agent: {
        type: 'python',
        builder: 'dockerfile',
        resources: {
          cpu: 'shared',
          memory: '256Mi',
          disk: '512Mi', // Ephemeral only
        },
        scaling: {
          minReplicas: 1,
          maxReplicas: 1, // No auto-scaling
          sleepOnInactivity: true,
          sleepTimeout: '30m',
        },
        startCommand: 'python -m uvicorn src.cofounder_agent.main:app --host 0.0.0.0 --port $PORT',
      },
    },
    env: {
      NODE_ENV: 'production',
      LOG_LEVEL: 'ERROR', // Only errors, reduce I/O
      DATABASE_POOL_SIZE: 5, // Minimal connections
      DATABASE_IDLE_TIMEOUT: 120000, // Close idle after 2min
      CACHE_TTL: 7200, // Cache longer (2 hours)
      ENABLE_COMPRESSION: true,
      API_RATE_LIMIT: 50, // Strict rate limiting
      CLEANUP_TEMP_FILES: true,
      ENABLE_PERFORMANCE_TRACING: false,
    },
  },
  vercel: {
    frontend: {
      plan: 'hobby', // Free tier
      scaling: 'none', // Static/serverless - no cost
      regions: ['us'], // Single region to reduce complexity
    },
  },
  ollama: {
    enabled: true,
    cost: 0, // FREE - local AI
    models: ['mistral', 'phi'], // Lightweight models
  },
};

// Create tier1 config file
const configPath = path.join(__dirname, '../.railway.tier1.json');
fs.writeFileSync(configPath, JSON.stringify(TIER1_CONFIG, null, 2));
console.log(`✅ Tier 1 configuration created: .railway.tier1.json`);

// Create environment file
const envContent = `
# ===== TIER 1 PRODUCTION (Ultra-Budget) =====
# Cost: ~$10-15/month
# Max Users: 50 concurrent
# SLA: ~95% uptime (services may sleep)

NODE_ENV=production
ENVIRONMENT=production

# ===== DATABASE (Minimal) =====
DATABASE_POOL_SIZE=5
DATABASE_IDLE_TIMEOUT=120000
DATABASE_STATEMENT_TIMEOUT=30000
DATABASE_CONNECTION_TIMEOUT=5000

# ===== CACHING (Essential only) =====
CACHE_TTL=7200
QUERY_CACHE_ENABLED=true
COMPRESSION_ENABLED=true

# ===== LOGGING (Errors only) =====
LOG_LEVEL=ERROR
ENABLE_DETAILED_LOGGING=false
ENABLE_PERFORMANCE_TRACING=false

# ===== API (Rate-limited) =====
API_RATE_LIMIT=50
API_TIMEOUT=15000
ENABLE_KEEP_ALIVE=true

# ===== STORAGE (Minimal) =====
MEDIA_UPLOAD_LIMIT=2097152
CLEANUP_TEMP_FILES=true
TEMP_FILE_RETENTION=43200

# ===== AI MODELS (Free/Local) =====
USE_OLLAMA=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODELS=mistral,phi

# ===== MONITORING (None - save costs) =====
ENABLE_MONITORING=false
METRICS_COLLECTION_ENABLED=false
ERROR_TRACKING_ENABLED=false
`;

const envPath = path.join(__dirname, '../.env.tier1.production');
fs.writeFileSync(envPath, envContent.trim());
console.log(`✅ Environment file created: .env.tier1.production`);

// Create cost analysis
const costAnalysis = `
# 💰 GLAD Labs Tier 1 Production Cost Analysis

## Monthly Cost Breakdown

| Service | Cost | Notes |
|---------|------|-------|
| PostgreSQL (Free tier) | $0 | Shared, 1GB storage, limited queries |
| Strapi CMS (Free tier) | $0 | 256MB RAM, shared CPU, sleeps after 30min |
| Co-Founder Agent (Free tier) | $0 | 256MB RAM, shared CPU, sleeps after 30min |
| Frontend (Vercel Hobby) | $0 | Free tier, unlimited bandwidth |
| Ollama (Local) | $0 | Free, self-hosted |
| **TOTAL** | **$0-10** | Only storage/egress if applicable |

## Cost Comparison

### Tier 1 (This Setup)
- Monthly: $0-10
- Users: 10-50
- Uptime: ~95%
- Response time: 2-5 sec (with sleep wakeup)
- Auto-scaling: ❌ No
- Guaranteed CPU: ❌ No

### Tier 2 (Budget)
- Monthly: $50-70
- Users: 100-500
- Uptime: 99.5%
- Response time: 200-500ms
- Auto-scaling: ✅ Yes
- Guaranteed CPU: ❌ No (shared)

### Tier 3 (Production)
- Monthly: $155+
- Users: 500-2000
- Uptime: 99.9%
- Response time: 50-200ms
- Auto-scaling: ✅ Yes
- Guaranteed CPU: ✅ Yes

## Tier 1 Limitations & Trade-offs

### ✅ Advantages
- Zero/minimal cost
- Perfect for MVP and testing
- No commitment
- Easy to scale up

### ⚠️ Trade-offs
- Services sleep after 30 min inactivity (first request takes 3-5 sec)
- Limited database queries (Railway free tier throttles)
- Shared CPU - slower response times under load
- 1GB database storage limit
- No guaranteed uptime SLA
- Single region
- Limited logs retention

### 🔧 Mitigation Strategies
- Keep services "warm" with health check endpoint (every 25 min)
- Cache aggressively (2-hour cache TTL)
- Implement request queuing for spikes
- Set up database cleanup to manage 1GB limit
- Use lightweight models (Mistral 7B, Phi) instead of large ones

## When to Scale Up to Tier 2

- 📊 Database approaching 1GB limit → Scale to Tier 2 ($50/mo)
- ⏱️ Response times consistently > 2 sec → Scale to Tier 2
- 👥 More than 50 concurrent users → Scale to Tier 2
- 📈 Daily active users > 500 → Scale to Tier 2

Upgrade cost: Only \$50-70/month, 7x capacity increase

## Deployment Strategy

### Week 1: Deploy & Test
- Deploy all services on Tier 1
- Test functionality and performance
- Set up monitoring and alerts
- Document baseline metrics

### Week 2: Optimize
- Implement caching and compression
- Optimize database queries
- Configure auto-sleep to prevent spikes
- Set up health check pings

### Week 3+: Monitor & Scale
- Monitor database size
- Track API response times
- Watch error rates
- Plan Tier 2 upgrade if needed

`;

const costPath = path.join(__dirname, '../TIER1_COST_ANALYSIS.md');
fs.writeFileSync(costPath, costAnalysis);
console.log(`✅ Cost analysis created: TIER1_COST_ANALYSIS.md`);

// Create health check configuration
const healthCheckConfig = `
/**
 * Keep services warm and prevent sleep
 * Deploy as Railway Cron Job every 25 minutes
 */

const healthChecks = [
  'https://api.your-site.com/api/health',
  'https://cms.your-site.com/admin',
  'https://your-site.vercel.app/',
];

async function keepServicesWarm() {
  console.log('[Health Check] Keeping services warm...');
  
  for (const url of healthChecks) {
    try {
      const response = await fetch(url, { timeout: 5000 });
      console.log(\`✅ \${url}: \${response.status}\`);
    } catch (error) {
      console.log(\`❌ \${url}: \${error.message}\`);
    }
  }
}

// Run immediately
keepServicesWarm().catch(console.error);
`;

const healthCheckPath = path.join(__dirname, '../scripts/tier1-health-check.js');
fs.writeFileSync(healthCheckPath, healthCheckConfig);
console.log(`✅ Health check script created: scripts/tier1-health-check.js`);

// Summary
console.log('\n' + '═'.repeat(60));
console.log('\n📊 Tier 1 Configuration Complete!');
console.log('\n🎯 Configuration Files Created:');
console.log('  ✅ .railway.tier1.json - Railway configuration');
console.log('  ✅ .env.tier1.production - Environment variables');
console.log('  ✅ TIER1_COST_ANALYSIS.md - Cost breakdown');
console.log('  ✅ scripts/tier1-health-check.js - Keep services warm');

console.log('\n💰 Monthly Cost: $0-10');
console.log('👥 Max Users: 50 concurrent');
console.log('⏱️  Response Time: 200-500ms avg (with sleep latency)');
console.log('📊 Database: 1GB limit');

console.log('\n🚀 Next Steps:');
console.log('  1. Review configuration: cat .railway.tier1.json');
console.log('  2. Deploy: npm run deploy:tier1');
console.log('  3. Monitor: npm run monitor:resources');
console.log('  4. Check costs: npm run cost:check');
console.log('  5. Set health check cron: every 25 minutes');

console.log('\n⚡ Scale Up to Tier 2 When:');
console.log('  - Database > 800MB');
console.log('  - Response time > 2 seconds consistently');
console.log('  - Users > 50 concurrent');
console.log('  - Daily actives > 500');

console.log('\n📚 Documentation: See TIER1_COST_ANALYSIS.md');
console.log('═'.repeat(60) + '\n');

process.exit(0);
