const axios = require('axios');

const STRAPI_URL =
  process.env.STRAPI_API_URL || 'https://strapi-production-b234.up.railway.app';
const API_URL = `${STRAPI_URL}/api`;

console.log('Starting Strapi content seeding...');
console.log('Strapi URL: ' + STRAPI_URL);

async function apiRequest(method, endpoint, data = null) {
  try {
    const apiToken = process.env.STRAPI_API_TOKEN;
    if (!apiToken) {
      console.error('ERROR: STRAPI_API_TOKEN not set');
      process.exit(1);
    }

    const config = {
      method: method,
      url: API_URL + endpoint,
      headers: {
        'Content-Type': 'application/json',
        Authorization: 'Bearer ' + apiToken,
      },
    };

    if (data) config.data = data;
    const response = await axios(config);
    return response.data;
  } catch (error) {
    if (error.response) {
      console.error(
        'API error: ' + method + ' ' + endpoint + ' ' + error.response.status
      );
    } else {
      console.error('Error: ' + error.message);
    }
    return null;
  }
}

async function checkHealth() {
  try {
    await axios.get(API_URL + '/users/me', {
      headers: { Authorization: 'Bearer ' + process.env.STRAPI_API_TOKEN },
    });
    console.log('Strapi is running');
    return true;
  } catch (e) {
    console.error('Cannot connect to Strapi');
    return false;
  }
}

const data = {
  categories: [
    { name: 'AI & Machine Learning', slug: 'ai-machine-learning' },
    { name: 'Game Development', slug: 'game-development' },
    { name: 'Technology Insights', slug: 'technology-insights' },
    { name: 'Business Strategy', slug: 'business-strategy' },
    { name: 'Innovation', slug: 'innovation' },
  ],
  tags: [
    { name: 'Artificial Intelligence', slug: 'artificial-intelligence' },
    { name: 'Gaming', slug: 'gaming' },
    { name: 'Neural Networks', slug: 'neural-networks' },
    { name: 'Deep Learning', slug: 'deep-learning' },
    { name: 'Computer Vision', slug: 'computer-vision' },
    { name: 'NLP', slug: 'nlp' },
    { name: 'Unity', slug: 'unity' },
    { name: 'Unreal Engine', slug: 'unreal-engine' },
    { name: 'Indie Games', slug: 'indie-games' },
    { name: 'Tech Trends', slug: 'tech-trends' },
    { name: 'Startups', slug: 'startups' },
    { name: 'Digital Transformation', slug: 'digital-transformation' },
  ],
  authors: [
    {
      name: 'Matthew M. Gladding',
      email: 'matthew@gladlabs.com',
      bio: 'Founder and CEO of Glad Labs.',
    },
    {
      name: 'AI Research Team',
      email: 'research@gladlabs.com',
      bio: 'Glad Labs AI research team.',
    },
  ],
};

async function findEntity(endpoint, field, value) {
  try {
    const url =
      API_URL +
      endpoint +
      '?filters[' +
      field +
      '][\]=' +
      encodeURIComponent(value);
    const response = await axios.get(url, {
      headers: { Authorization: 'Bearer ' + process.env.STRAPI_API_TOKEN },
    });
    return response.data?.data?.length > 0 ? response.data.data[0] : null;
  } catch (e) {
    return null;
  }
}

async function seedAll() {
  try {
    if (!(await checkHealth())) process.exit(1);

    console.log('Creating categories...');
    for (const cat of data.categories) {
      await apiRequest('POST', '/categories', { data: cat });
    }

    console.log('Creating tags...');
    for (const tag of data.tags) {
      await apiRequest('POST', '/tags', { data: tag });
    }

    console.log('Creating authors...');
    for (const author of data.authors) {
      await apiRequest('POST', '/authors', { data: author });
    }

    console.log('Done!');
    process.exit(0);
  } catch (error) {
    console.error('Failed: ' + error.message);
    process.exit(1);
  }
}

seedAll();
