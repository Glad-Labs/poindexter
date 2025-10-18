/**
 * Strapi Admin Panel Configuration
 *
 * Railway proxy terminates SSL (HTTPS → HTTP internally)
 * We set secure: false to let Railway handle SSL wrapping
 * This works for both local HTTP and Railway HTTPS without detection
 *
 * @see https://docs.strapi.io/dev-docs/configurations/admin-panel
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
        secure: false, // Railway proxy handles SSL wrapping
        httpOnly: true,
        sameSite: 'lax',
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
