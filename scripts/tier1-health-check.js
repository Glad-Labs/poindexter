/**
 * Keep services warm and prevent sleep
 * Deploy as Railway Cron Job every 25 minutes
 */

const healthChecks = [
  'https://api.your-site.com/api/health',
  'https://cms.your-site.com/admin',
  'https://your-site.vercel.app/',
];

async function keepServicesWarm() {
  console.log('[Health Check] Keeping services warm...');

  for (const url of healthChecks) {
    try {
      const response = await fetch(url, { timeout: 5000 });
      console.log(`✅ ${url}: ${response.status}`);
    } catch (error) {
      console.log(`❌ ${url}: ${error.message}`);
    }
  }
}

// Run immediately
keepServicesWarm().catch(console.error);
