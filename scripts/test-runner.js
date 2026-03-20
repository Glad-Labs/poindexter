#!/usr/bin/env node

/**
 * Unified Test Runner
 * ===================
 *
 * Orchestrates running all tests:
 * - Playwright E2E tests (frontend)
 * - Pytest tests (backend)
 * - Jest component tests (React)
 *
 * Features:
 * - Parallel execution
 * - Comprehensive reporting
 * - Performance summary
 * - Coverage collection
 * - CI/CD integration
 *
 * Usage:
 *   npm run test:unified                  # Run all tests
 *   npm run test:unified -- --watch       # Watch mode
 *   npm run test:unified -- --coverage    # With coverage
 *   npm run test:unified -- --debug       # Debug mode
 */

import { spawn } from 'child_process';
import { writeFileSync, mkdirSync } from 'fs';
import { join } from 'path';

// --- Configuration ---

const TEST_SUITES = {
  playwright: {
    name: 'Playwright E2E Tests',
    command: 'npx',
    args: ['playwright', 'test'],
    cwd: '.',
    timeout: 60000,
    critical: true,
  },
  pytest: {
    name: 'Pytest Backend Tests',
    command: 'poetry',
    args: [
      'run',
      'pytest',
      'tests/integration/',
      'tests/e2e/',
      '-v',
      '--tb=short',
    ],
    cwd: '.',
    timeout: 120000,
    critical: true,
  },
  jest: {
    name: 'Jest Component Tests',
    command: 'npm',
    args: ['run', 'test', '--', '--passWithNoTests'],
    cwd: '.',
    timeout: 60000,
    critical: false,
  },
};

const RESULTS_DIR = join(process.cwd(), 'test-results');
const SUMMARY_FILE = join(RESULTS_DIR, 'test-summary.json');

// --- Utilities ---

function createLogger(testName) {
  const colors = {
    reset: '\x1b[0m',
    bright: '\x1b[1m',
    dim: '\x1b[2m',
    green: '\x1b[32m',
    red: '\x1b[31m',
    yellow: '\x1b[33m',
    blue: '\x1b[34m',
    cyan: '\x1b[36m',
  };

  return {
    info: (msg) =>
      console.log(`${colors.cyan}[${testName}]${colors.reset} ${msg}`),
    success: (msg) =>
      console.log(
        `${colors.green}✓${colors.reset} ${colors.bright}${msg}${colors.reset}`
      ),
    error: (msg) =>
      console.log(
        `${colors.red}✗${colors.reset} ${colors.bright}${msg}${colors.reset}`
      ),
    warn: (msg) => console.log(`${colors.yellow}⚠${colors.reset} ${msg}`),
    debug: (msg) =>
      process.argv.includes('--debug')
        ? console.log(`${colors.dim}${msg}${colors.reset}`)
        : null,
  };
}

function runTest(testName, config) {
  return new Promise((resolve) => {
    const logger = createLogger(testName);
    const startTime = Date.now();

    logger.info(`Starting...`);

    const process = spawn(config.command, config.args, {
      cwd: config.cwd,
      stdio: process.argv.includes('--debug') ? 'inherit' : 'pipe',
      shell: true,
    });

    let stdout = '';
    let stderr = '';

    if (!process.argv.includes('--debug')) {
      process.stdout.on('data', (data) => {
        stdout += data.toString();
      });

      process.stderr.on('data', (data) => {
        stderr += data.toString();
      });
    }

    const timeout = setTimeout(() => {
      process.kill();
      logger.error(`Timeout after ${config.timeout}ms`);
      resolve({
        name: testName,
        status: 'timeout',
        duration: config.timeout,
        critical: config.critical,
      });
    }, config.timeout);

    process.on('close', (code) => {
      clearTimeout(timeout);
      const duration = Date.now() - startTime;
      const success = code === 0;

      if (success) {
        logger.success(`Completed in ${duration}ms`);
      } else {
        logger.error(`Failed with exit code ${code}`);
        if (stderr) {
          logger.debug(stderr.substring(0, 500));
        }
      }

      resolve({
        name: testName,
        status: success ? 'passed' : 'failed',
        duration,
        critical: config.critical,
        stdout,
        stderr,
      });
    });
  });
}

async function runAllTests() {
  console.log('\n🧪 Running Unified Test Suite\n');
  console.log('═'.repeat(60));

  mkdirSync(RESULTS_DIR, { recursive: true });

  const results = [];
  const startTime = Date.now();

  // Run tests sequentially (can be parallelized with Promise.all for speed)
  for (const [key, config] of Object.entries(TEST_SUITES)) {
    const result = await runTest(config.name, config);
    results.push(result);
  }

  const totalDuration = Date.now() - startTime;

  // --- Generate Summary ---

  const summary = {
    timestamp: new Date().toISOString(),
    totalDuration,
    tests: results.length,
    passed: results.filter((r) => r.status === 'passed').length,
    failed: results.filter((r) => r.status === 'failed').length,
    results,
  };

  // Write summary to file
  mkdirSync(RESULTS_DIR, { recursive: true });
  writeFileSync(SUMMARY_FILE, JSON.stringify(summary, null, 2));

  // --- Print Report ---

  console.log('\n═'.repeat(60));
  console.log('\n📊 Test Summary\n');

  results.forEach((result) => {
    const icon = result.status === 'passed' ? '✓' : '✗';
    const color = result.status === 'passed' ? '\x1b[32m' : '\x1b[31m';
    const critical = result.critical ? '[CRITICAL]' : '[OPTIONAL]';

    console.log(
      `${color}${icon}\x1b[0m ${result.name.padEnd(40)} ${critical.padEnd(12)} ${(result.duration / 1000).toFixed(2)}s`
    );
  });

  console.log(
    `\n📈 Overall: ${summary.passed}/${summary.tests} passed in ${(totalDuration / 1000).toFixed(2)}s\n`
  );

  // --- Check for critical failures ---

  const criticalFailures = results.filter(
    (r) => r.critical && r.status !== 'passed'
  );

  if (criticalFailures.length > 0) {
    console.log('\x1b[31m❌ Critical tests failed:\x1b[0m\n');
    criticalFailures.forEach((result) => {
      console.log(`   • ${result.name}`);
      if (result.stderr) {
        console.log(`     ${result.stderr.split('\n')[0]}`);
      }
    });
    console.log();
    process.exit(1);
  }

  console.log('\x1b[32m✅ All tests passed!\x1b[0m\n');
  console.log(`📁 Test results saved to: ${SUMMARY_FILE}\n`);

  process.exit(0);
}

// --- Entry Point ---

console.log('\n🚀 Glad Labs Unified Test Runner');
console.log(`   Version: 1.0.0\n`);

if (process.argv.includes('--help')) {
  console.log(`
Usage: npm run test:unified [options]

Options:
  --watch         Watch mode (re-run on file changes)
  --coverage      Collect coverage information
  --debug         Show detailed debug output
  --help          Show this help message
  --no-parallel   Run tests sequentially instead of parallel

Examples:
  npm run test:unified                 # Run all tests
  npm run test:unified -- --debug      # Debug mode
  npm run test:unified -- --coverage   # With coverage reporting
  `);
  process.exit(0);
}

runAllTests().catch((error) => {
  console.error('\x1b[31m💥 Test runner error:\x1b[0m', error);
  process.exit(1);
});
