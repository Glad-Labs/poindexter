const fetch = require('node-fetch');

async function diagnoseContentTypes() {
  try {
    console.log('🔍 Diagnosing Strapi content types...\n');

    // Test the content-type-builder API to see what Strapi knows about
    const contentTypesResponse = await fetch(
      'http://localhost:1337/content-type-builder/content-types'
    );

    if (contentTypesResponse.ok) {
      const contentTypes = await contentTypesResponse.json();
      console.log('✅ Content types known to Strapi:');
      console.log(JSON.stringify(contentTypes, null, 2));
    } else {
      console.log(
        `❌ Cannot access content-type-builder API: ${contentTypesResponse.status}`
      );
    }

    // Test individual API endpoints
    console.log('\n🧪 Testing API endpoints:');

    const endpoints = [
      'categories',
      'posts',
      'authors',
      'tags',
      'content-metrics',
    ];

    for (const endpoint of endpoints) {
      try {
        const response = await fetch(`http://localhost:1337/api/${endpoint}`);
        console.log(`${endpoint}: ${response.status} ${response.statusText}`);
      } catch (error) {
        console.log(`${endpoint}: ERROR - ${error.message}`);
      }
    }
  } catch (error) {
    console.error('❌ Diagnosis failed:', error.message);
  }
}

if (require.main === module) {
  diagnoseContentTypes();
}

module.exports = { diagnoseContentTypes };
