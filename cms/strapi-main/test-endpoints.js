const axios = require('axios');

const STRAPI_URL = 'http://localhost:1337';
const API_URL = `${STRAPI_URL}/api`;
const TOKEN =
  '16b333c5bf909389523c48cb461ec2aeb260c3439026a82b02d6c8aa554ad93af6055f3fe0e420157926ac0f83d5f8e05bb7535c8944b3a033cc275c5a7be45a';

async function testEndpoints() {
  const endpoints = [
    '/categories',
    '/posts',
    '/tags',
    '/authors',
    '/users',
    '/content-types',
  ];

  for (const endpoint of endpoints) {
    try {
      console.log(`\nTesting: ${endpoint}`);

      // Try with auth
      const resp = await axios.get(`${API_URL}${endpoint}`, {
        headers: { Authorization: `Bearer ${TOKEN}` },
        timeout: 3000,
        validateStatus: () => true, // Don't throw on any status
      });

      console.log(`  Status: ${resp.status}`);
      if (resp.status === 200) {
        const count = Array.isArray(resp.data?.data)
          ? resp.data.data.length
          : '?';
        console.log(`  ✅ OK - ${count} items`);
      } else if (resp.status === 401) {
        console.log(`  ❌ Unauthorized (401)`);
      } else if (resp.status === 404) {
        console.log(`  ⚠️ Not Found (404)`);
      } else {
        console.log(`  Status: ${resp.status}`);
      }
    } catch (e) {
      console.log(`  Error: ${e.message}`);
    }
  }
}

testEndpoints();
