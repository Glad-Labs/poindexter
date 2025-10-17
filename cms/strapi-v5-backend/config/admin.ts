/**
 * Strapi Admin Panel Configuration
 * 
 * Railway.app Cookie Settings:
 * - cookieSecure: false - Railway's proxy handles SSL (HTTPS → HTTP internally)
 * - Setting secure=true causes "Cannot send secure cookie over unencrypted connection"
 * 
 * Security Note: External users still connect via HTTPS (Railway proxy)
 * 
 * @see https://docs.strapi.io/dev-docs/configurations/admin-panel
 * @see https://docs.railway.app/deploy/deployments#https-and-ssl
 */
export default ({ env }) => ({
  auth: {
    secret: env('ADMIN_JWT_SECRET'), // Used to sign admin JWT tokens
    options: {
      cookieSecure: false, // CRITICAL: Disable for Railway proxy SSL termination
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
