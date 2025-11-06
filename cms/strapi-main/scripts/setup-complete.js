#!/usr/bin/env node

/**
 * Complete Strapi Setup & Initialization
 *
 * This master script performs all setup steps in the correct order:
 * 1. Wait for Strapi to be ready
 * 2. Register content type schemas
 * 3. Enable API permissions
 * 4. Seed sample data
 *
 * Run this ONCE after starting Strapi for the first time
 *
 * Usage:
 *   npm run setup:complete
 */

const { spawn } = require('child_process');
const axios = require('axios');
const fs = require('fs');

const STRAPI_URL = process.env.STRAPI_API_URL || 'http://localhost:1337';
const MAX_RETRIES = 30;
const RETRY_DELAY = 1000;

console.log('╔════════════════════════════════════════════════════════╗');
console.log('║        Glad Labs - Complete Strapi Setup               ║');
console.log('╚════════════════════════════════════════════════════════╝\n');

/**
 * Wait for Strapi to be ready
 */
async function waitForStrapi() {
  console.log('⏳ Waiting for Strapi to be ready...\n');

  for (let i = 0; i < MAX_RETRIES; i++) {
    try {
      const response = await axios.get(`${STRAPI_URL}/admin`);
      console.log('✅ Strapi is ready!\n');
      return true;
    } catch (error) {
      process.stdout.write('.');
      await new Promise((resolve) => setTimeout(resolve, RETRY_DELAY));
    }
  }

  console.error('\n❌ Strapi did not start within timeout');
  process.exit(1);
}

/**
 * Run a setup script as subprocess
 */
function runScript(scriptName, description) {
  return new Promise((resolve, reject) => {
    console.log(`\n${'─'.repeat(55)}`);
    console.log(`📝 Step: ${description}`);
    console.log(`${'─'.repeat(55)}\n`);

    const script = require.resolve(`./${scriptName}`);

    const child = spawn('node', [script], {
      stdio: 'inherit',
      env: {
        ...process.env,
        STRAPI_API_URL: STRAPI_URL,
      },
    });

    child.on('close', (code) => {
      if (code === 0) {
        resolve();
      } else {
        reject(new Error(`${scriptName} exited with code ${code}`));
      }
    });
  });
}

/**
 * Main setup flow
 */
async function setup() {
  try {
    // Step 1: Wait for Strapi
    await waitForStrapi();

    // Step 2: Register content types
    try {
      await runScript(
        'register-content-types.js',
        'Register Content Type Schemas'
      );
    } catch (error) {
      console.warn(`⚠️  Content type registration: ${error.message}`);
      console.warn('   Continuing with setup...\n');
    }

    // Step 3: Seed data (optional - only if flag set)
    if (process.env.SEED_DATA === 'true') {
      try {
        await runScript('seed-data-fixed.js', 'Seed Sample Data');
      } catch (error) {
        console.warn(`⚠️  Data seeding: ${error.message}`);
      }
    }

    console.log('\n╔════════════════════════════════════════════════════════╗');
    console.log('║   ✅ STRAPI SETUP COMPLETE                             ║');
    console.log('╚════════════════════════════════════════════════════════╝\n');

    console.log('🎉 Your Strapi instance is ready!\n');
    console.log('📌 Quick Links:');
    console.log(`   Admin Dashboard:  ${STRAPI_URL}/admin`);
    console.log(`   API Documentation: ${STRAPI_URL}/api/docs`);
    console.log(`   Test API:          curl ${STRAPI_URL}/api/posts\n`);

    console.log('📝 Next Steps:');
    console.log('   1. Create admin account in Strapi (if not already done)');
    console.log('   2. Verify content types appear in Content Manager');
    console.log('   3. Enable API permissions (Settings → Roles)');
    console.log('   4. Test API endpoints from frontend\n');

    process.exit(0);
  } catch (error) {
    console.error('\n❌ Setup failed:', error.message);
    process.exit(1);
  }
}

setup();
