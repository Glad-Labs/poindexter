const axios = require('axios');

const STRAPI_URL = process.env.STRAPI_API_URL || 'http://localhost:1337';
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
        'API error: ' +
          method +
          ' ' +
          endpoint +
          ' Status: ' +
          error.response.status,
        'Data:',
        error.response.data?.error?.message
      );
    } else {
      console.error('Error: ' + error.message);
    }
    return null;
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

async function seedAll() {
  try {
    // Skip health check - Strapi is clearly running
    console.log('Skipping health check...\n');

    console.log('Creating categories...');
    let count = 0;
    for (const cat of data.categories) {
      const result = await apiRequest('POST', '/categories', {
        data: { ...cat, publishedAt: new Date() },
      });
      if (result) {
        count++;
        console.log(`  ✅ ${cat.name}`);
      } else {
        console.log(`  ❌ ${cat.name} (failed)`);
      }
    }
    console.log(`Created ${count}/${data.categories.length} categories\n`);

    console.log('Creating tags...');
    count = 0;
    for (const tag of data.tags) {
      const result = await apiRequest('POST', '/tags', {
        data: { ...tag, publishedAt: new Date() },
      });
      if (result) {
        count++;
        console.log(`  ✅ ${tag.name}`);
      } else {
        console.log(`  ❌ ${tag.name} (failed)`);
      }
    }
    console.log(`Created ${count}/${data.tags.length} tags\n`);

    console.log('Creating authors...');
    count = 0;
    for (const author of data.authors) {
      const result = await apiRequest('POST', '/authors', {
        data: { ...author, publishedAt: new Date() },
      });
      if (result) {
        count++;
        console.log(`  ✅ ${author.name}`);
      } else {
        console.log(`  ❌ ${author.name} (failed)`);
      }
    }
    console.log(`Created ${count}/${data.authors.length} authors\n`);

    console.log('Done!');
    process.exit(0);
  } catch (error) {
    console.error('Failed: ' + error.message);
    process.exit(1);
  }
}

seedAll();
