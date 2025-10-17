/**
 * Strapi Server Configuration
 *
 * Railway.app Production Settings:
 * - Proxy: Railway terminates SSL, so we must trust the proxy headers
 * - Port: Railway sets PORT=5000 via environment variable
 * - URL: Public-facing URL for generating absolute links
 *
 * @see https://docs.strapi.io/dev-docs/configurations/server
 * @see https://docs.railway.app
 */
export default ({ env }) => ({
  host: env('HOST', '0.0.0.0'),
  port: env.int('PORT', 1337),
  url: env('URL'),
  proxy: true,
  app: {
    keys: env.array('APP_KEYS'),
  },
  admin: {
    auth: {
      // This is the crucial part to force secure admin cookies
      secure: env.bool('ADMIN_AUTH_SECURE', true),
    },
  },
});
