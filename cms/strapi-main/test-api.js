const axios = require('axios');

const STRAPI_URL = 'http://localhost:1337';
const API_URL = \/api;
const TOKEN = '16b333c5bf909389523c48cb461ec2aeb260c3439026a82b02d6c8aa554ad93af6055f3fe0e420157926ac0f83d5f8e05bb7535c8944b3a033cc275c5a7be45a';

async function testAuth() {
  try {
    console.log('Testing API token...');
    const response = await axios.get(\/content-type-builder/components, {
      headers: { Authorization: Bearer \16b333c5bf909389523c48cb461ec2aeb260c3439026a82b02d6c8aa554ad93af6055f3fe0e420157926ac0f83d5f8e05bb7535c8944b3a033cc275c5a7be45a }
    });
    console.log(' Auth works!');
  } catch (e) {
    console.log(' Auth failed:', e.response?.status, e.response?.data?.error?.message);
  }
}

testAuth();
