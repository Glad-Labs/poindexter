/**
 * Global Test Teardown
 * ====================
 *
 * Runs once after all tests across all browsers
 * Perfect for:
 * - Cleanup operations
 * - Test summary reporting
 * - Database cleanup
 * - Performance metric collection
 */

import { FullConfig } from '@playwright/test';
import * as fs from 'fs';
import * as path from 'path';

async function globalTeardown(config: FullConfig) {
  console.log('\n🧹 Global Test Teardown Started');

  try {
    // Ensure test results directory exists
    const resultsDir = path.join(process.cwd(), 'test-results');
    if (!fs.existsSync(resultsDir)) {
      fs.mkdirSync(resultsDir, { recursive: true });
    }

    // Parse and summarize JSON results if available
    const jsonResultPath = path.join(resultsDir, 'results.json');
    if (fs.existsSync(jsonResultPath)) {
      const results = JSON.parse(fs.readFileSync(jsonResultPath, 'utf-8'));
      const stats = {
        total: results.stats?.expected || 0,
        passed: results.stats?.expected || 0,
        failed: results.stats?.unexpected || 0,
        skipped: results.stats?.skipped || 0,
        duration: results.stats?.duration || 0,
      };

      console.log(`
📊 Test Summary:
   ├─ Total Tests: ${stats.total}
   ├─ Passed: ${stats.passed}
   ├─ Failed: ${stats.failed}
   ├─ Skipped: ${stats.skipped}
   └─ Duration: ${(stats.duration / 1000).toFixed(2)}s
      `);
    }

    console.log('✅ Global Test Teardown Complete');
  } catch (error) {
    console.warn('⚠️  Teardown warning:', error);
  }
}

export default globalTeardown;
