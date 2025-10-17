/**
 * Strapi Middleware Configuration
 *
 * Railway.app Session Cookie Settings:
 * - secure: false - Railway's proxy terminates SSL (connection is HTTP internally)
 * - httpOnly: true - Prevent JavaScript access to cookies (XSS protection)
 * - sameSite: 'lax' - Allow cookies with cross-site requests (needed for admin panel)
 *
 * Security Note: External traffic is still HTTPS (Railway's proxy layer)
 *
 * @see https://docs.strapi.io/dev-docs/configurations/middlewares
 * @see https://docs.railway.app/deploy/deployments#https-and-ssl
 */
export default [
  'global::force-https', // CRITICAL: Must be first
  'strapi::logger',
  'strapi::errors',
  'strapi::security',
  'strapi::cors',
  'strapi::poweredBy',
  'strapi::query',
  'strapi::body',
  'strapi::session',
  'strapi::favicon',
  'strapi::public',
];
