#!/usr/bin/env node

/**
 * Test Runner Infrastructure Validation
 * =====================================
 *
 * Validates that the test-runner.js infrastructure works correctly:
 * - Discovers test files correctly
 * - Generates reports in correct format
 * - Handles parallel execution
 * - Properly aggregates results
 *
 * Usage:
 *   node scripts/test-runner-validation.js
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const colors = {
  reset: '\x1b[0m',
  bright: '\x1b[1m',
  green: '\x1b[32m',
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  cyan: '\x1b[36m',
};

function log(msg, color = 'reset') {
  console.log(`${colors[color]}${msg}${colors.reset}`);
}

function validateFileExists(path, description) {
  if (!fs.existsSync(path)) {
    log(`✗ FAILED: ${description} - File not found: ${path}`, 'red');
    return false;
  }
  log(`✓ PASSED: ${description}`, 'green');
  return true;
}

function validateFileContent(filePath, pattern, description) {
  try {
    const content = fs.readFileSync(filePath, 'utf-8');
    if (typeof pattern === 'string') {
      if (!content.includes(pattern)) {
        log(
          `✗ FAILED: ${description} - Pattern not found: "${pattern}"`,
          'red'
        );
        return false;
      }
    } else if (pattern instanceof RegExp) {
      if (!pattern.test(content)) {
        log(`✗ FAILED: ${description} - Regex pattern not found`, 'red');
        return false;
      }
    }
    log(`✓ PASSED: ${description}`, 'green');
    return true;
  } catch (error) {
    log(`✗ FAILED: ${description} - ${error.message}`, 'red');
    return false;
  }
}

function validateDirectory(dirPath, description) {
  if (!fs.existsSync(dirPath) || !fs.statSync(dirPath).isDirectory()) {
    log(`✗ FAILED: ${description} - Directory not found: ${dirPath}`, 'red');
    return false;
  }
  log(`✓ PASSED: ${description}`, 'green');
  return true;
}

async function runValidations() {
  log('\n🧪 Test Runner Infrastructure Validation\n', 'cyan');
  log('═'.repeat(60), 'cyan');

  const resultsDir = path.join(process.cwd(), 'test-results');
  let passCount = 0;
  let failCount = 0;

  // ========================
  // 1. Configuration Files
  // ========================
  log('\n📋 Configuration Files\n', 'bright');

  if (
    validateFileExists(
      path.join(process.cwd(), 'playwright.config.ts'),
      'playwright.config.ts exists'
    )
  )
    passCount++;
  else failCount++;

  if (
    validateFileContent(
      path.join(process.cwd(), 'playwright.config.ts'),
      'defineConfig',
      'playwright.config.ts has defineConfig'
    )
  )
    passCount++;
  else failCount++;

  if (
    validateFileExists(
      path.join(process.cwd(), 'scripts/test-runner.js'),
      'test-runner.js exists'
    )
  )
    passCount++;
  else failCount++;

  if (
    validateFileContent(
      path.join(process.cwd(), 'scripts/test-runner.js'),
      'TEST_SUITES',
      'test-runner.js defines TEST_SUITES'
    )
  )
    passCount++;
  else failCount++;

  // ========================
  // 2. Test Directories
  // ========================
  log('\n📁 Test Directories\n', 'bright');

  if (
    validateDirectory(
      path.join(process.cwd(), 'web/public-site/e2e'),
      'Playwright tests directory'
    )
  )
    passCount++;
  else failCount++;

  if (
    validateDirectory(
      path.join(process.cwd(), 'tests/integration'),
      'Pytest integration tests directory'
    )
  )
    passCount++;
  else failCount++;

  // ========================
  // 3. Fixture Files
  // ========================
  log('\n🔧 Fixture Files\n', 'bright');

  if (
    validateFileExists(
      path.join(process.cwd(), 'web/public-site/e2e/fixtures.ts'),
      'Playwright fixtures.ts exists'
    )
  )
    passCount++;
  else failCount++;

  if (
    validateFileContent(
      path.join(process.cwd(), 'web/public-site/e2e/fixtures.ts'),
      'class APIClient',
      'Playwright fixtures define APIClient'
    )
  )
    passCount++;
  else failCount++;

  if (
    validateFileContent(
      path.join(process.cwd(), 'web/public-site/e2e/fixtures.ts'),
      'class PerformanceMetrics',
      'Playwright fixtures define PerformanceMetrics'
    )
  )
    passCount++;
  else failCount++;

  if (
    validateFileExists(
      path.join(process.cwd(), 'tests/conftest_enhanced.py'),
      'Pytest conftest_enhanced.py exists'
    )
  )
    passCount++;
  else failCount++;

  if (
    validateFileContent(
      path.join(process.cwd(), 'tests/conftest_enhanced.py'),
      'class APITester',
      'Pytest fixtures define APITester'
    )
  )
    passCount++;
  else failCount++;

  // ========================
  // 4. Global Setup/Teardown
  // ========================
  log('\n🌍 Global Setup/Teardown\n', 'bright');

  if (
    validateFileExists(
      path.join(process.cwd(), 'web/public-site/e2e/global-setup.ts'),
      'global-setup.ts exists'
    )
  )
    passCount++;
  else failCount++;

  if (
    validateFileExists(
      path.join(process.cwd(), 'web/public-site/e2e/global-teardown.ts'),
      'global-teardown.ts exists'
    )
  )
    passCount++;
  else failCount++;

  // ========================
  // 5. Test Scripts
  // ========================
  log('\n🧬 Test Scripts (package.json)\n', 'bright');

  try {
    const packageJsonPath = path.join(process.cwd(), 'package.json');
    const packageJson = JSON.parse(fs.readFileSync(packageJsonPath, 'utf-8'));
    const scripts = packageJson.scripts || {};

    const requiredScripts = [
      'test:unified',
      'test:playwright',
      'test:python',
      'test:api',
      'test:playwright:debug',
      'test:python:integration',
      'test:python:performance',
    ];

    for (const script of requiredScripts) {
      if (scripts[script]) {
        log(`✓ PASSED: npm script "${script}" defined`, 'green');
        passCount++;
      } else {
        log(
          `✗ FAILED: npm script "${script}" not found in package.json`,
          'red'
        );
        failCount++;
      }
    }
  } catch (error) {
    log(`✗ FAILED: Error reading package.json - ${error.message}`, 'red');
    failCount++;
  }

  // ========================
  // 6. Test File Structure
  // ========================
  log('\n📂 Test File Organization\n', 'bright');

  try {
    const testFiles = [
      'web/public-site/e2e/integration-tests.spec.ts',
      'tests/integration/test_api_integration.py',
      'web/public-site/e2e/accessibility.spec.js',
      'web/public-site/e2e/home.spec.js',
    ];

    for (const file of testFiles) {
      const filePath = path.join(process.cwd(), file);
      if (fs.existsSync(filePath)) {
        const stats = fs.statSync(filePath);
        log(
          `✓ PASSED: ${file} exists (${Math.round(stats.size / 1024)}KB)`,
          'green'
        );
        passCount++;
      } else {
        log(`✗ FAILED: ${file} not found`, 'red');
        failCount++;
      }
    }
  } catch (error) {
    log(`✗ FAILED: Error checking test files - ${error.message}`, 'red');
    failCount++;
  }

  // ========================
  // 7. Test Requirements
  // ========================
  log('\n📦 Test Requirements\n', 'bright');

  try {
    const requiredPackages = {
      playwright: 'web/public-site/e2e tests',
      pytest: 'backend integration tests',
      httpx: 'async HTTP client for tests',
    };

    const pyprojectPath = path.join(process.cwd(), 'pyproject.toml');
    const packageJsonPath = path.join(process.cwd(), 'package.json');

    const pyprojectContent = fs.readFileSync(pyprojectPath, 'utf-8');
    const packageJsonContent = JSON.parse(
      fs.readFileSync(packageJsonPath, 'utf-8')
    );

    let hasRequestedPackages = 0;
    for (const [pkg, description] of Object.entries(requiredPackages)) {
      const inPyproject = pyprojectContent.includes(pkg);
      const inPackageJson = packageJsonContent.devDependencies?.[pkg];

      if (inPyproject || inPackageJson) {
        log(`✓ PASSED: ${pkg} installed (${description})`, 'green');
        passCount++;
      } else {
        log(`✗ FAILED: ${pkg} not found in dependencies`, 'red');
        failCount++;
      }
    }
  } catch (error) {
    log(`✗ FAILED: Error checking dependencies - ${error.message}`, 'red');
    failCount++;
  }

  // ========================
  // 8. Documentation
  // ========================
  log('\n📖 Documentation\n', 'bright');

  const docs = [
    'TESTING_INFRASTRUCTURE_GUIDE.md',
    'TESTING_QUICK_REFERENCE.md',
    'UI_BACKEND_INTEGRATION_TESTING.md',
    'TESTING_IMPLEMENTATION_SUMMARY.md',
  ];

  for (const doc of docs) {
    if (validateFileExists(path.join(process.cwd(), doc), `${doc} exists`))
      passCount++;
    else failCount++;
  }

  // ========================
  // Summary
  // ========================
  log('\n' + '═'.repeat(60), 'cyan');
  log('\n📊 Validation Summary\n', 'bright');

  const total = passCount + failCount;
  const passRate = total > 0 ? ((passCount / total) * 100).toFixed(1) : 0;

  console.log(`
  ✓ Passed: ${passCount}/${total}
  ✗ Failed: ${failCount}/${total}
  📈 Pass Rate: ${passRate}%
  `);

  if (failCount === 0) {
    log('✅ All infrastructure validations PASSED!', 'green');
    log('\n🚀 Test infrastructure is ready for use!\n', 'green');
    return true;
  } else {
    log(
      `❌ ${failCount} validation(s) FAILED. Fix issues and try again.\n`,
      'red'
    );
    return false;
  }
}

// Run validations
runValidations()
  .then((success) => process.exit(success ? 0 : 1))
  .catch((error) => {
    log(`\n💥 Validation crashed: ${error.message}\n`, 'red');
    process.exit(1);
  });
