#!/usr/bin/env node

/**
 * Environment Selection Script
 *
 * Automatically selects the correct .env file based on current git branch
 *
 * Usage:
 *   node scripts/select-env.js
 *   npm run env:select
 *
 * Branch → Environment Mapping:
 *   main             → .env.production
 *   staging          → .env.staging
 *   dev*, feat/*     → .env (local development)
 *   other            → .env (local development, default)
 */

const { execSync } = require('child_process');
const fs = require('fs');
const path = require('path');

function log(message, type = 'info') {
  const colors = {
    info: '\x1b[36m', // Cyan
    success: '\x1b[32m', // Green
    warning: '\x1b[33m', // Yellow
    error: '\x1b[31m', // Red
    reset: '\x1b[0m',
  };

  const color = colors[type] || colors.info;
  console.log(`${color}${message}${colors.reset}`);
}

try {
  // Get current branch
  let branch;
  try {
    branch = execSync('git rev-parse --abbrev-ref HEAD', {
      encoding: 'utf-8',
    }).trim();
  } catch (error) {
    log('❌ Not in a git repository or git not installed', 'error');
    process.exit(1);
  }

  // Determine environment and env file
  let envFile;
  let envLabel;
  let nodeEnv;

  if (branch === 'main') {
    envFile = '.env.production';
    envLabel = 'PRODUCTION';
    nodeEnv = 'production';
  } else if (branch === 'staging') {
    envFile = '.env.staging';
    envLabel = 'STAGING';
    nodeEnv = 'staging';
  } else if (branch.startsWith('dev') || branch.startsWith('feat')) {
    envFile = '.env';
    envLabel = 'LOCAL DEVELOPMENT';
    nodeEnv = 'development';
  } else {
    log(`⚠️ Unknown branch: ${branch}`, 'warning');
    envFile = '.env';
    envLabel = 'LOCAL DEVELOPMENT (default)';
    nodeEnv = 'development';
  }

  // Set environment variable for current process
  process.env.NODE_ENV = nodeEnv;

  // Source directory
  const sourceDir = path.join(__dirname, '..');
  const sourceFile = path.join(sourceDir, envFile);
  const destFile = path.join(sourceDir, '.env.local');

  // Check if source file exists
  if (!fs.existsSync(sourceFile)) {
    const exampleFile = path.join(sourceDir, '.env.example');

    if (!fs.existsSync(exampleFile)) {
      log(`❌ ${envFile} not found and .env.example not available`, 'error');
      process.exit(1);
    }

    log(`⚠️ ${envFile} not found, using .env.example as fallback`, 'warning');
    fs.copyFileSync(exampleFile, destFile);
  } else {
    // Copy env file to .env.local
    fs.copyFileSync(sourceFile, destFile);
  }

  // Display success message
  log(`\n📦 Environment Selection`, 'success');
  log(`   Branch: ${branch}`, 'info');
  log(`   Environment: ${envLabel}`, 'info');
  log(`   Source: ${envFile}`, 'info');
  log(`   Loaded: .env.local`, 'info');
  log(`   NODE_ENV: ${nodeEnv}\n`, 'info');

  // Show some loaded variables (first few lines)
  const fileContent = fs.readFileSync(destFile, 'utf-8');
  const lines = fileContent
    .split('\n')
    .filter((line) => line.trim() && !line.startsWith('#'))
    .slice(0, 3);

  if (lines.length > 0) {
    log('📋 First few variables:', 'info');
    lines.forEach((line) => {
      const [key] = line.split('=');
      log(`   ${key}=***`, 'info');
    });
  }

  log(`✅ Ready to run!`, 'success');
} catch (error) {
  log(`❌ Error: ${error.message}`, 'error');
  process.exit(1);
}
