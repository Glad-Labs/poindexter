export default ({ env }) => ({
  host: env('HOST', '0.0.0.0'),
  port: env.int('PORT', 1337),
  app: {
    keys: env.array('APP_KEYS'),
  },
  proxy: env.bool('TRUST_PROXY', true), // Trust Railway proxy
  url: env(
    'PUBLIC_URL',
    'https://glad-labs-strapi-v5-backend-production.up.railway.app'
  ), // Public URL for admin panel
});
