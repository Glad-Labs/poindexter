#!/usr/bin/env node

/**
 * Health Check Script
 * Verifies that all services (backend, public site, oversight hub) are running and responsive
 */

const http = require('http');

const services = [
  { name: 'Backend', url: 'http://localhost:8000/health', port: 8000 },
  { name: 'Public Site', url: 'http://localhost:3000', port: 3000 },
  { name: 'Oversight Hub', url: 'http://localhost:3001', port: 3001 },
];

async function checkService(service) {
  return new Promise((resolve) => {
    const req = http.get(service.url, { timeout: 5000 }, (res) => {
      const status =
        res.statusCode === 200 || res.statusCode === 404 ? '✓' : '✗';
      console.log(`[${service.port}] ${service.name} ${status}`);
      resolve(res.statusCode < 500);
    });

    req.on('error', () => {
      console.log(`[${service.port}] ${service.name} ✗ (not running)`);
      resolve(false);
    });

    req.on('timeout', () => {
      req.destroy();
      console.log(`[${service.port}] ${service.name} ✗ (timeout)`);
      resolve(false);
    });
  });
}

async function runHealthCheck() {
  console.log('\n🏥 Service Health Check\n');

  const results = await Promise.all(services.map(checkService));
  const allHealthy = results.every((r) => r);

  if (allHealthy) {
    console.log('\n✅ All services healthy\n');
    process.exit(0);
  } else {
    console.log(
      '\n⚠️  Some services are not responding. Start with: npm run dev\n'
    );
    process.exit(1);
  }
}

runHealthCheck();
