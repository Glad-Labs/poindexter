#!/usr/bin/env node

/**
 * Railway Environment Variable Validator
 * Run this to check if your Strapi environment is correctly configured
 *
 * Usage: railway shell
 *        node validate-env.js
 */

const requiredVars = {
  // Database
  DATABASE_CLIENT: {
    value: undefined,
    type: 'string',
    desc: 'Must be "postgres"',
  },
  DATABASE_URL: {
    value: undefined,
    type: 'string',
    desc: 'PostgreSQL connection string',
  },

  // Server
  URL: {
    value: undefined,
    type: 'string',
    desc: 'Production domain with https://',
  },
  HOST: { value: undefined, type: 'string', desc: 'Usually 0.0.0.0 or ::' },
  PORT: { value: undefined, type: 'number', desc: 'Usually 1337' },

  // Security - should be set by Railway as secrets
  ADMIN_JWT_SECRET: {
    value: undefined,
    type: 'secret',
    desc: 'JWT secret for admin',
  },
  APP_KEYS: {
    value: undefined,
    type: 'secret',
    desc: 'Session encryption keys',
  },
  API_TOKEN_SALT: { value: undefined, type: 'secret', desc: 'API token salt' },
  TRANSFER_TOKEN_SALT: {
    value: undefined,
    type: 'secret',
    desc: 'Transfer token salt',
  },
};

console.log('🔍 Railway Environment Variable Validator\n');
console.log('='.repeat(60));

let allGood = true;

// Check each variable
Object.entries(requiredVars).forEach(([key, meta]) => {
  const value = process.env[key];
  const hasValue = !!value;

  if (!hasValue) {
    console.log(`\n❌ MISSING: ${key}`);
    console.log(`   Description: ${meta.desc}`);
    console.log(`   Type: ${meta.type}`);
    console.log(`   Fix: Set this in Railway Variables`);
    allGood = false;
  } else {
    // Mask sensitive values
    let display = value;
    if (meta.type === 'secret') {
      display =
        value.substring(0, 20) +
        '...' +
        (value.length > 20 ? ` (${value.length} chars)` : '');
    }

    console.log(`\n✅ ${key}`);
    console.log(`   Value: ${display}`);

    // Validate specific requirements
    if (key === 'DATABASE_CLIENT' && value !== 'postgres') {
      console.log(`   ⚠️  WARNING: Must be 'postgres', got '${value}'`);
      allGood = false;
    }

    if (key === 'URL' && !value.startsWith('https://')) {
      console.log(`   ⚠️  WARNING: Must start with https://, got '${value}'`);
      allGood = false;
    }

    if (key === 'HOST' && !['0.0.0.0', '::', 'localhost'].includes(value)) {
      console.log(`   ⚠️  WARNING: Usually 0.0.0.0 or ::, got '${value}'`);
    }
  }
});

console.log('\n' + '='.repeat(60));

if (allGood) {
  console.log('\n✨ All environment variables are correctly set!\n');
  process.exit(0);
} else {
  console.log(
    '\n⚠️  Some environment variables are missing or misconfigured.\n'
  );
  console.log('Fix these on Railway dashboard:');
  console.log('1. Go to your Strapi service');
  console.log('2. Click "Variables"');
  console.log('3. Add missing variables');
  console.log('4. Redeploy\n');
  process.exit(1);
}
