/**
 * Strapi Server Configuration
 *
 * Railway.app Production Settings:
 * - Proxy: Railway terminates SSL, so we must trust the proxy headers
 * - Port: Railway sets PORT=5000 via environment variable
 * - URL: Public-facing URL for generating absolute links
 * - Trust Proxy: Enable X-Forwarded-* headers for HTTPS detection
 *
 * @see https://docs.strapi.io/dev-docs/configurations/server
 * @see https://railway.app
 */
export default ({ env }) => ({
  host: env('HOST', '0.0.0.0'),
  port: env.int('PORT', 1337),
  proxy: {
    enabled: true,
    trust: ['127.0.0.1', 'loopback', 'linklocal', 'uniquelocal'], // Trust proxy headers
  },
  app: {
    keys: env.array('APP_KEYS'),
  },
  url: env('URL'),
});
