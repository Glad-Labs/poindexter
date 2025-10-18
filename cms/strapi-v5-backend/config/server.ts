/**
 * Strapi Server Configuration
 *
 * Matches Railway template which works correctly on both local and production.
 * The simple `proxy: true` tells Strapi to trust reverse proxy headers.
 * Strapi v5 automatically detects HTTPS via X-Forwarded-Proto when this is enabled.
 *
 * @see https://docs.strapi.io/dev-docs/configurations/server
 * @see https://github.com/railwayapp-templates/strapi
 */
export default ({ env }) => ({
  host: env('HOST', '0.0.0.0'),
  port: env.int('PORT', 1337),
  app: {
    keys: env.array('APP_KEYS'),
  },
  webhooks: {
    populateRelations: env.bool('WEBHOOKS_POPULATE_RELATIONS', false),
  },
  url: env('URL'),
  proxy: true,
});
