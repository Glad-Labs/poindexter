const axios = require('axios');

const STRAPI_URL = 'http://localhost:1337';
const API_URL = `${STRAPI_URL}/api`;
const TOKEN =
  '16b333c5bf909389523c48cb461ec2aeb260c3439026a82b02d6c8aa554ad93af6055f3fe0e420157926ac0f83d5f8e05bb7535c8944b3a033cc275c5a7be45a';

async function test() {
  try {
    console.log('Attempting to create a category...\n');

    const data = {
      data: {
        name: 'Test Category',
        slug: 'test-category',
      },
    };

    const response = await axios.post(`${API_URL}/categories`, data, {
      headers: {
        Authorization: `Bearer ${TOKEN}`,
        'Content-Type': 'application/json',
      },
      timeout: 5000,
      validateStatus: () => true, // Don't throw
    });

    console.log('Status:', response.status);
    console.log('Response:', JSON.stringify(response.data, null, 2));
  } catch (e) {
    console.log('Request error:', e.message);
  }
}

test();
