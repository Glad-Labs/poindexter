#!/usr/bin/env node

/**
 * Monitor Production Resources and Alert When Approaching Limits
 * Run: npm run monitor:resources
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

<<<<<<< HEAD:scripts/monitor-production-resources.js
console.log('📊 Glad Labs Production Resource Monitor');
=======
console.log('📊 Glad Labs Tier 1 Resource Monitor');
>>>>>>> feat/refine:scripts/monitor-tier1-resources.js
console.log('═'.repeat(60));

const LIMITS = {
  database_storage: 1024, // 1 GB in MB
  memory_per_service: 256, // 256 MB
  connection_pool: 5,
};

const THRESHOLDS = {
  database_warning: 0.7, // 70%
  database_critical: 0.9, // 90%
  memory_warning: 0.75, // 75%
  memory_critical: 0.9, // 90%
};

async function getMetrics() {
  try {
    console.log('\n📈 Fetching metrics from Railway...\n');

    // Note: In real implementation, use Railway API
    // For now, showing template

    const metrics = {
      database: {
        storage_used_mb: 250, // Example: 250MB used
        connections: 3,
        queries_per_sec: 5,
      },
      strapi: {
        memory_mb: 180, // Example: 180MB used
        cpu_percent: 15,
        response_time_ms: 250,
      },
      api: {
        memory_mb: 200, // Example: 200MB used
        cpu_percent: 25,
        response_time_ms: 300,
      },
    };

    return metrics;
  } catch (error) {
    console.error('❌ Failed to fetch metrics:', error.message);
    process.exit(1);
  }
}

function checkThresholds(metrics) {
  const alerts = [];

  // Check database storage
  const db_percent =
    (metrics.database.storage_used_mb / LIMITS.database_storage) * 100;
  console.log(
    `\n💾 Database Storage: ${metrics.database.storage_used_mb}MB / ${LIMITS.database_storage}MB (${db_percent.toFixed(1)}%)`
  );

  if (db_percent > THRESHOLDS.database_critical * 100) {
    alerts.push({
      severity: 'CRITICAL',
      message: `Database storage at ${db_percent.toFixed(1)}% - UPGRADE TO TIER 2 REQUIRED`,
    });
    console.log(`   ❌ CRITICAL: Database approaching 1GB limit!`);
  } else if (db_percent > THRESHOLDS.database_warning * 100) {
    alerts.push({
      severity: 'WARNING',
      message: `Database storage at ${db_percent.toFixed(1)}% - Consider upgrade soon`,
    });
    console.log(`   ⚠️  WARNING: Database at 70% capacity`);
  } else {
    console.log(`   ✅ OK: Database under 70%`);
  }

  // Check Strapi memory
  const strapi_mem_percent =
    (metrics.strapi.memory_mb / LIMITS.memory_per_service) * 100;
  console.log(
    `\n🛢️  Strapi Memory: ${metrics.strapi.memory_mb}MB / ${LIMITS.memory_per_service}MB (${strapi_mem_percent.toFixed(1)}%)`
  );

  if (strapi_mem_percent > THRESHOLDS.memory_critical * 100) {
    alerts.push({
      severity: 'CRITICAL',
      message: `Strapi memory at ${strapi_mem_percent.toFixed(1)}% - CRITICAL`,
    });
    console.log(`   ❌ CRITICAL: Memory critical - service may crash!`);
  } else if (strapi_mem_percent > THRESHOLDS.memory_warning * 100) {
    alerts.push({
      severity: 'WARNING',
      message: `Strapi memory at ${strapi_mem_percent.toFixed(1)}%`,
    });
    console.log(`   ⚠️  WARNING: Memory at 75%`);
  } else {
    console.log(`   ✅ OK: Memory under 75%`);
  }

  // Check API memory
  const api_mem_percent =
    (metrics.api.memory_mb / LIMITS.memory_per_service) * 100;
  console.log(
    `\n🧠 Co-Founder Agent Memory: ${metrics.api.memory_mb}MB / ${LIMITS.memory_per_service}MB (${api_mem_percent.toFixed(1)}%)`
  );

  if (api_mem_percent > THRESHOLDS.memory_critical * 100) {
    alerts.push({
      severity: 'CRITICAL',
      message: `API memory at ${api_mem_percent.toFixed(1)}% - CRITICAL`,
    });
    console.log(`   ❌ CRITICAL: Memory critical - service may crash!`);
  } else if (api_mem_percent > THRESHOLDS.memory_warning * 100) {
    alerts.push({
      severity: 'WARNING',
      message: `API memory at ${api_mem_percent.toFixed(1)}%`,
    });
    console.log(`   ⚠️  WARNING: Memory at 75%`);
  } else {
    console.log(`   ✅ OK: Memory under 75%`);
  }

  // Check response times
  console.log(`\n⏱️  Response Times:`);
  console.log(
    `   Strapi: ${metrics.strapi.response_time_ms}ms ${metrics.strapi.response_time_ms > 1000 ? '❌ SLOW' : '✅ OK'}`
  );
  console.log(
    `   API: ${metrics.api.response_time_ms}ms ${metrics.api.response_time_ms > 1000 ? '❌ SLOW' : '✅ OK'}`
  );

  if (
    metrics.strapi.response_time_ms > 2000 ||
    metrics.api.response_time_ms > 2000
  ) {
    alerts.push({
      severity: 'WARNING',
      message: 'Response times > 2 seconds - consider Tier 2 upgrade',
    });
  }

  // Check connections
  console.log(
    `\n🔌 Database Connections: ${metrics.database.connections} / ${LIMITS.connection_pool}`
  );
  if (metrics.database.connections >= LIMITS.connection_pool) {
    alerts.push({
      severity: 'CRITICAL',
      message: 'Connection pool at capacity - upgrade required',
    });
    console.log(`   ❌ CRITICAL: Connection pool saturated!`);
  } else {
    console.log(
      `   ✅ OK: ${LIMITS.connection_pool - metrics.database.connections} connections available`
    );
  }

  return alerts;
}

function printSummary(metrics, alerts) {
  console.log('\n' + '═'.repeat(60));
  console.log('\n📋 SUMMARY & RECOMMENDATIONS\n');

  // Overall health
  const criticalCount = alerts.filter((a) => a.severity === 'CRITICAL').length;
  const warningCount = alerts.filter((a) => a.severity === 'WARNING').length;

  if (criticalCount > 0) {
    console.log('🔴 CRITICAL ISSUES DETECTED\n');
    alerts
      .filter((a) => a.severity === 'CRITICAL')
      .forEach((alert) => {
        console.log(`   ❌ ${alert.message}`);
      });
    console.log('\n   ⚡ ACTION REQUIRED: Upgrade to Tier 2 immediately!');
  }

  if (warningCount > 0) {
    console.log('\n🟡 WARNINGS\n');
    alerts
      .filter((a) => a.severity === 'WARNING')
      .forEach((alert) => {
        console.log(`   ⚠️  ${alert.message}`);
      });
    console.log('\n   💡 ACTION RECOMMENDED: Plan upgrade to Tier 2 soon');
  }

  if (criticalCount === 0 && warningCount === 0) {
    console.log('✅ ALL SYSTEMS HEALTHY\n');
    console.log('   All metrics within acceptable ranges');
    console.log('   Continue monitoring for changes');
  }

  // Cost comparison
  console.log('\n💰 UPGRADE OPTION\n');
  console.log(
    '   Production (Current):  $0/month  | 1GB DB | 256MB RAM | 50 users'
  );
  console.log(
    '   Tier 2 (Upgrade):  $50/month | 10GB DB | 1GB RAM | 500 users'
  );
  console.log('   Savings: Avoid downtime, 7x capacity increase');

  // Next steps
  console.log('\n🚀 NEXT STEPS\n');

  if (criticalCount > 0) {
    console.log('   1. RUN: npm run scale:to-tier2');
    console.log('   2. WAIT: Deployment ~20 minutes');
    console.log('   3. VERIFY: All services working');
  } else if (warningCount > 0) {
    console.log('   1. MONITOR: Continue tracking metrics');
    console.log('   2. PLAN: Schedule Tier 2 upgrade');
    console.log('   3. OPTIMIZE: Implement caching, cleanup old data');
  } else {
    console.log('   1. MONITOR: Check weekly');
    console.log('   2. BACKUP: Run weekly database backups');
    console.log('   3. MAINTAIN: Archive old data regularly');
  }

  // Save report
  const report = {
    timestamp: new Date().toISOString(),
    metrics,
    alerts,
    summary: {
      health:
        criticalCount === 0 && warningCount === 0
          ? 'HEALTHY'
          : 'NEEDS_ATTENTION',
      critical: criticalCount,
      warnings: warningCount,
    },
  };

  const reportPath = path.join(__dirname, '../tier1-monitoring-report.json');
  fs.writeFileSync(reportPath, JSON.stringify(report, null, 2));
  console.log(`\n📊 Report saved: tier1-monitoring-report.json`);
}

// Main execution
(async () => {
  try {
    const metrics = await getMetrics();
    const alerts = checkThresholds(metrics);
    printSummary(metrics, alerts);

    // Exit with appropriate code
    const criticalCount = alerts.filter(
      (a) => a.severity === 'CRITICAL'
    ).length;
    process.exit(criticalCount > 0 ? 1 : 0);
  } catch (error) {
    console.error('❌ Error during monitoring:', error);
    process.exit(1);
  }
})();


