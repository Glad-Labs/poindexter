#!/usr/bin/env node

/**
 * Link Environment Script
 *
 * Copies root .env.local to React/Next.js workspace directories
 * Ensures all frontend apps have access to root environment configuration
 *
 * Monorepo Design: Single .env.local at root, shared across all packages
 *
 * Usage:
 *   node scripts/link-env.js
 *   npm run link:env
 */

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
  const rootDir = process.cwd();
  const rootEnvPath = path.join(rootDir, '.env.local');

  // Check if root .env.local exists
  if (!fs.existsSync(rootEnvPath)) {
    log(
      '⚠️ Root .env.local not found. Skipping environment linking.',
      'warning'
    );
    process.exit(0);
  }

  // Read root .env.local
  const envContent = fs.readFileSync(rootEnvPath, 'utf8');

  // Workspace directories that need the env file
  const workspaces = ['web/public-site', 'web/oversight-hub'];

  log('\n📦 Linking Root .env.local to Workspaces\n', 'info');

  workspaces.forEach((workspace) => {
    const workspaceEnvPath = path.join(rootDir, workspace, '.env.local');

    try {
      if (fs.existsSync(workspaceEnvPath)) {
        fs.unlinkSync(workspaceEnvPath);
        log(`🧹 Removed existing ${workspace}/.env.local`, 'warning');
      }
      // Copy root .env.local to workspace
      fs.copyFileSync(rootEnvPath, workspaceEnvPath);
      log(`✅ ${workspace}/.env.local`, 'success');
    } catch (error) {
      log(
        `❌ Failed to link ${workspace}/.env.local: ${error.message}`,
        'error'
      );
      process.exit(1);
    }
  });

  log('\n✅ All workspaces linked to root .env.local\n', 'success');
  log('Note: Environment variables are sourced from root .env.local', 'info');
  log('To update: Edit /root/.env.local and run: npm run link:env\n', 'info');
} catch (error) {
  log(`\n❌ Error: ${error.message}\n`, 'error');
  process.exit(1);
}
