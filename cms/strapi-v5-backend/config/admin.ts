/**
 * Strapi Admin Panel Configuration
 *
 * Matches Railway template defaults which work correctly on both local and production.
 * Do NOT explicitly configure cookies - let Strapi use its defaults.
 * Strapi v5 automatically detects HTTPS via X-Forwarded-Proto when proxy is enabled.
 *
 * @see https://docs.strapi.io/dev-docs/configurations/admin-panel
 * @see https://github.com/railwayapp-templates/strapi
 */
export default ({ env }) => ({
  auth: {
    secret: env('ADMIN_JWT_SECRET'),
  },
  apiToken: {
    salt: env('API_TOKEN_SALT'),
  },
  transfer: {
    token: {
      salt: env('TRANSFER_TOKEN_SALT'),
    },
  },
  flags: {
    nps: env.bool('FLAG_NPS', true),
    promoteEE: env.bool('FLAG_PROMOTE_EE', true),
  },
});
