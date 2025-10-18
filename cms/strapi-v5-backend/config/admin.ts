/**
 * Strapi Admin Panel Configuration
 *
 * Railway proxy terminates SSL (HTTPS → HTTP internally)
 * Koa (Node.js framework) automatically detects HTTPS via X-Forwarded-Proto header
 * Set secure: true so cookies work over HTTPS on Railway
 *
 * @see https://docs.strapi.io/dev-docs/configurations/admin-panel
 * @see https://koajs.com/
 */
export default ({ env }) => ({
  url: env('ADMIN_URL', '/admin'),
  serveAdminPanel: true,
  auth: {
    secret: env('ADMIN_JWT_SECRET'),
    sessions: {
      maxSessionLifespan: 1000 * 60 * 60 * 24 * 7,
      maxRefreshTokenLifespan: 1000 * 60 * 60 * 24 * 30,
      cookie: {
        secure: true, // Set to true - Koa detects HTTPS via X-Forwarded-Proto
        httpOnly: true,
        sameSite: 'strict',
      },
    },
  },
  apiToken: {
    salt: env('API_TOKEN_SALT'),
  },
  transfer: {
    token: {
      salt: env('TRANSFER_TOKEN_SALT'),
    },
  },
  appEncryptionKey: env('APP_ENCRYPTION_KEY'),
  flags: {
    nps: env.bool('FLAG_NPS', true),
    promoteEE: env.bool('FLAG_PROMOTE_EE', true),
  },
});
