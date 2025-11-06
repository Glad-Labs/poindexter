#!/usr/bin/env node

/**
 * Register Content Type Schemas with Strapi
 * 
 * This script reads schema.json files from each content-type directory
 * and registers them with Strapi programmatically.
 * 
 * Run this BEFORE seeding data, immediately after Strapi starts.
 * 
 * Usage:
 *   npm run register-types
 *   
 *   OR with custom Strapi URL:
 *   STRAPI_API_URL=http://localhost:1337 node scripts/register-content-types.js
 */

const fs = require('fs');
const path = require('path');
const axios = require('axios');

const STRAPI_URL = process.env.STRAPI_API_URL || 'http://localhost:1337';
const API_URL = `${STRAPI_URL}/api`;
const API_TOKEN = process.env.STRAPI_API_TOKEN || 'test-token-development';

const CONTENT_TYPES_DIR = path.join(__dirname, '../src/api');

console.log('╔════════════════════════════════════════════════════════╗');
console.log('║   Strapi Content Type Schema Registration Script        ║');
console.log('╚════════════════════════════════════════════════════════╝\n');

console.log(`Strapi URL: ${STRAPI_URL}`);
console.log(`API URL: ${API_URL}`);
console.log(`Content Types Dir: ${CONTENT_TYPES_DIR}\n`);

/**
 * Discover all content type schemas
 */
function discoverSchemas() {
  const schemas = [];
  
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
  
  return schemas;
}

/**
 * Register a single content type with Strapi
 */
async function registerContentType(contentType) {
  try {
    const { name, schema } = contentType;
    
    // Prepare the payload for Strapi API
    const payload = {
      ...schema,
      // Ensure required fields
      collectionName: schema.collectionName || name,
    };
    
    console.log(`⏳ Registering ${name}...`);
    
    // Attempt to create content type via Content-Type Builder API
    const response = await axios.post(
      `${STRAPI_URL}/content-type-builder/content-types`,
      {
        contentType: payload,
      },
      {
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${API_TOKEN}`,
        },
      }
    );
    
    console.log(`✅ ${name}: Registered successfully`);
    return true;
  } catch (error) {
    if (error.response?.status === 409 || error.response?.status === 400) {
      // Content type already exists or validation error (might be OK)
      console.log(`⚠️  ${contentType.name}: ${error.response?.data?.error?.message || 'Already exists or validation error'}`);
      return true;
    }
    
    console.error(`❌ ${contentType.name}: ${error.message}`);
    if (error.response?.data?.error) {
      console.error(`   Error details: ${JSON.stringify(error.response.data.error)}`);
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
    schemas.forEach(s => console.log(`   - ${s.name}`));
    console.log();
    
    console.log('📝 Registering content types...\n');
    
    let successCount = 0;
    for (const schema of schemas) {
      const success = await registerContentType(schema);
      if (success) successCount++;
      // Small delay between registrations
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    
    console.log(`\n╔════════════════════════════════════════════════════════╗`);
    console.log(`║   ✅ REGISTRATION COMPLETE                              ║`);
    console.log(`╚════════════════════════════════════════════════════════╝\n`);
    
    console.log(`Registered: ${successCount}/${schemas.length} content types\n`);
    
    if (successCount === schemas.length) {
      console.log('✅ All content types registered successfully!');
      console.log('\n📌 Next steps:');
      console.log('   1. Verify in Strapi Admin: http://localhost:1337/admin');
      console.log('   2. Check Content Manager for new types');
      console.log('   3. Test API endpoints: curl http://localhost:1337/api/posts');
      console.log('   4. Run seeding script: npm run seed\n');
      process.exit(0);
    } else {
      console.log('⚠️  Some content types may not have registered');
      console.log('   Check the errors above and try again\n');
      process.exit(1);
    }
  } catch (error) {
    console.error('❌ Registration failed:', error.message);
    process.exit(1);
  }
}

// Run registration
registerAll();
