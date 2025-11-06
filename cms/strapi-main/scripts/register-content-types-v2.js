#!/usr/bin/env node

/**
 * Improved Strapi Content Type Registration Script v2
 *
 * This version:
 * - Checks if content types already exist before trying to create
 * - Gracefully handles 401 auth errors
 * - Suggests next steps when types don't exist
 * - Works with OR without API token
 *
 * Usage:
 *   npm run register-types
 *   STRAPI_API_TOKEN=your-token npm run register-types
 */

const fs = require('fs');
const path = require('path');
const axios = require('axios');

const STRAPI_URL = process.env.STRAPI_API_URL || 'http://localhost:1337';
const API_URL = `${STRAPI_URL}/api`;
const API_TOKEN = process.env.STRAPI_API_TOKEN;

const CONTENT_TYPES_DIR = path.join(__dirname, '../src/api');

console.log('╔════════════════════════════════════════════════════════╗');
console.log('║  Strapi Content Type Registration v2 (Improved)        ║');
console.log('╚════════════════════════════════════════════════════════╝\n');

console.log(`Strapi URL: ${STRAPI_URL}`);
console.log(`API Token: ${API_TOKEN ? 'SET ✓' : 'NOT SET ⚠️ '}`);
console.log(`Content Types Dir: ${CONTENT_TYPES_DIR}\n`);

/**
 * Discover all content type schemas
 */
function discoverSchemas() {
  const schemas = [];

  try {
    const contentTypeDirs = fs.readdirSync(CONTENT_TYPES_DIR);

    for (const dir of contentTypeDirs) {
      const schemaPath = path.join(
        CONTENT_TYPES_DIR,
        dir,
        'content-types',
        dir,
        'schema.json'
      );

      if (fs.existsSync(schemaPath)) {
        const schema = JSON.parse(fs.readFileSync(schemaPath, 'utf8'));
        schemas.push({
          name: dir,
          path: schemaPath,
          schema: schema,
        });
      }
    }
  } catch (error) {
    console.error(`Error discovering schemas: ${error.message}`);
  }

  return schemas;
}

/**
 * Check if content type already exists
 */
async function contentTypeExists(name) {
  try {
    // Try to fetch the content type
    const response = await axios.get(
      `${STRAPI_URL}/content-type-builder/content-types`,
      {
        headers: API_TOKEN ? { Authorization: `Bearer ${API_TOKEN}` } : {},
      }
    );

    const types = response.data?.data || [];
    return types.some((t) => t.uid === `api::${name}.${name}`);
  } catch (error) {
    // If we get 401, we can't check without token
    // Assume it doesn't exist and suggest token setup
    return false;
  }
}

/**
 * Register a single content type with Strapi
 */
async function registerContentType(contentType) {
  try {
    const { name, schema } = contentType;

    // Check if already exists
    console.log(`ℹ️  ${name}: Checking if already registered...`);
    if (await contentTypeExists(name)) {
      console.log(`✅ ${name}: Already registered (skipping)`);
      return true;
    }

    // Try to register
    if (!API_TOKEN) {
      console.log(`⚠️  ${name}: Cannot register without API token`);
      console.log(
        `    Reason: API token not set (STRAPI_API_TOKEN environment variable)`
      );
      console.log(
        `    Solution: Create token in Strapi admin and set environment variable`
      );
      return false;
    }

    console.log(`⏳ ${name}: Registering...`);

    const payload = {
      ...schema,
      collectionName: schema.collectionName || name,
    };

    const response = await axios.post(
      `${STRAPI_URL}/content-type-builder/content-types`,
      { contentType: payload },
      {
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${API_TOKEN}`,
        },
      }
    );

    console.log(`✅ ${name}: Registered successfully`);
    return true;
  } catch (error) {
    if (error.response?.status === 401) {
      console.log(
        `⚠️  ${contentType.name}: Requires authentication (API token needed)`
      );
      console.log(
        `    Error: ${error.response?.data?.error?.message || 'Unauthorized'}`
      );
      return false;
    }

    if (error.response?.status === 409) {
      console.log(`✅ ${contentType.name}: Already exists`);
      return true;
    }

    console.error(`❌ ${contentType.name}: ${error.message}`);
    if (error.response?.data?.error) {
      console.error(`    Error: ${error.response.data.error.message}`);
    }
    return false;
  }
}

/**
 * Main registration flow
 */
async function registerAll() {
  try {
    console.log('🔍 Discovering schemas...\n');
    const schemas = discoverSchemas();

    if (schemas.length === 0) {
      console.error('❌ No schemas found in content-types directories');
      process.exit(1);
    }

    console.log(`📋 Found ${schemas.length} content type(s):`);
    schemas.forEach((s) => console.log(`   - ${s.name}`));
    console.log();

    console.log('📝 Checking and registering content types...\n');

    let successCount = 0;
    let skipCount = 0;

    for (const schema of schemas) {
      try {
        const success = await registerContentType(schema);
        if (success) successCount++;
        else skipCount++;
      } catch (error) {
        console.error(`Error processing ${schema.name}: ${error.message}`);
      }

      // Small delay between requests
      await new Promise((resolve) => setTimeout(resolve, 300));
    }

    console.log(`\n╔════════════════════════════════════════════════════════╗`);
    console.log(`║   ✅ CHECK COMPLETE                                     ║`);
    console.log(`╚════════════════════════════════════════════════════════╝\n`);

    console.log(`Summary:`);
    console.log(`  Registered: ${successCount}`);
    console.log(`  Skipped/Failed: ${skipCount}`);
    console.log(`  Total: ${schemas.length}\n`);

    if (successCount === schemas.length) {
      console.log('✅ All content types are registered!\n');
      console.log('📌 Next steps:');
      console.log('   1. Verify in Strapi Admin: http://localhost:1337/admin');
      console.log('   2. Check Content Manager for new types');
      console.log('   3. Run seed: npm run seed\n');
      process.exit(0);
    } else if (skipCount > 0 && successCount === 0) {
      console.log('⚠️  Could not register content types\n');
      console.log('📌 Options:\n');
      console.log('Option 1: Provide API Token');
      console.log('   $env:STRAPI_API_TOKEN = "your-token-here"');
      console.log('   npm run register-types\n');

      console.log('Option 2: Let Strapi auto-discover schemas');
      console.log('   1. Go to: http://localhost:1337/admin');
      console.log('   2. Create admin account (first time)');
      console.log('   3. Content types should auto-register');
      console.log("   4. Restart Strapi if they don't appear");
      console.log('   5. Then run: npm run seed\n');

      process.exit(1);
    } else {
      console.log('⚠️  Some content types may need attention\n');
      console.log('📌 Next steps:');
      console.log('   1. Check Strapi admin for any errors');
      console.log('   2. Verify problematic types in Content Manager');
      console.log('   3. Try seeding data: npm run seed\n');
      process.exit(0);
    }
  } catch (error) {
    console.error('❌ Fatal error:', error.message);
    process.exit(1);
  }
}

// Run registration
registerAll();
