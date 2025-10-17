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
  proxy: true,
  app: {
    keys: env.array('APP_KEYS'),
    proxy: true,
  },
  url: env('URL'),
});
