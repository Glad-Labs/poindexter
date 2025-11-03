const axios = require('axios');

const STRAPI_URL = 'http://localhost:1337';
const API_URL = `${STRAPI_URL}/api`;
const TOKEN =
  '16b333c5bf909389523c48cb461ec2aeb260c3439026a82b02d6c8aa554ad93af6055f3fe0e420157926ac0f83d5f8e05bb7535c8944b3a033cc275c5a7be45a';

async function test() {
  try {
    console.log('1. Testing Strapi connectivity...');
    const healthResp = await axios.get(`${STRAPI_URL}/`);
    console.log('✅ Strapi is running');

    console.log('\n2. Testing API token with simple GET...');
    const testResp = await axios.get(`${API_URL}/categories?limit=1`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
      timeout: 5000,
    });
    console.log('✅ API token works!');
    console.log('   Response:', testResp.data);
  } catch (e) {
    console.log('❌ Error:');
    console.log('   Message:', e.message);
    if (e.response) {
      console.log('   Status:', e.response.status);
      console.log('   Error:', e.response.data?.error?.message);
    }
  }
}

test();
