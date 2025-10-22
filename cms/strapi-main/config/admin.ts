/**
 * Strapi Admin Panel Configuration
 *
 * Railway.app Cookie Settings:
 * - Railway's proxy handles SSL (HTTPS → HTTP internally)
 * - Must explicitly set URLs to use HTTPS protocol
 * - External users still connect via HTTPS (Railway proxy layer)
 *
 * @see https://docs.strapi.io/dev-docs/configurations/admin-panel
 * @see https://docs.railway.app/deploy/deployments#https-and-ssl
 */
export default ({ env }) => ({
  // Explicitly set admin URLs to use HTTPS
  url: env('ADMIN_URL', '/admin'),
  serveAdminPanel: true,
  auth: {
    secret: env('ADMIN_JWT_SECRET'), // Used to sign admin JWT tokens
    sessions: {
      maxSessionLifespan: 1000 * 60 * 60 * 24 * 7, // 7 days
      maxRefreshTokenLifespan: 1000 * 60 * 60 * 24 * 30, // 30 days
      // Admin refresh token cookie settings (Strapi v5+)
      cookie: {
        // Railway: secure=true requires NODE_ENV=production in Railway dashboard
        // Local: secure=false for http://localhost
        secure:
          env('NODE_ENV') === 'production' ||
          env.bool('FORCE_SECURE_COOKIE', false),
        httpOnly: true, // Prevent XSS attacks
        sameSite: 'lax', // Allow navigation from external sites
      },
    },
  },
  apiToken: {
    salt: env('API_TOKEN_SALT'), // Used to salt API tokens
  },
  transfer: {
    token: {
      salt: env('TRANSFER_TOKEN_SALT'), // Used for content transfer tokens
    },
  },
  appEncryptionKey: env('APP_ENCRYPTION_KEY'), // App-level encryption key (32 bytes base64)
  flags: {
    nps: env.bool('FLAG_NPS', true), // Net Promoter Score survey
    promoteEE: env.bool('FLAG_PROMOTE_EE', true), // Promote Enterprise Edition
  },
});
