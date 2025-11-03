const axios = require('axios');

const STRAPI_URL = 'http://localhost:1337';
const API_URL = `${STRAPI_URL}/api`;
const TOKEN =
  '16b333c5bf909389523c48cb461ec2aeb260c3439026a82b02d6c8aa554ad93af6055f3fe0e420157926ac0f83d5f8e05bb7535c8944b3a033cc275c5a7be45a';

async function test() {
  try {
    console.log('Testing GET /api/categories...\n');

    const response = await axios.get(`${API_URL}/categories`, {
      headers: { Authorization: `Bearer ${TOKEN}` },
      timeout: 5000,
      validateStatus: () => true,
    });

    console.log('Status:', response.status);
    console.log('Full URL:', `${API_URL}/categories`);
    console.log('Headers sent:', { Authorization: `Bearer [REDACTED]` });

    if (response.data) {
      console.log('Response type:', typeof response.data);
      console.log(
        'Response:',
        JSON.stringify(response.data, null, 2).substring(0, 500)
      );
    } else {
      console.log('Response: (empty)');
    }
  } catch (e) {
    console.log('Request error:', e.message);
  }
}

test();
