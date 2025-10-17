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
  'strapi::logger',
  'strapi::errors',
  {
    name: 'strapi::security',
    config: {
      contentSecurityPolicy: {
        useDefaults: true,
        directives: {
          'connect-src': ["'self'", 'https:', 'http:'],
          'img-src': [
            "'self'",
            'data:',
            'blob:',
            'market-assets.strapi.io',
            'res.cloudinary.com',
          ],
          'media-src': [
            "'self'",
            'data:',
            'blob:',
            'market-assets.strapi.io',
            'res.cloudinary.com',
          ],
          upgradeInsecureRequests: null,
        },
      },
      ip: {
        trusted: [],
      },
    },
  },
  'strapi::cors',
  'strapi::poweredBy',
  'strapi::query',
  'strapi::body',
  {
    name: 'strapi::session',
    config: {
      cookie: {
        secure: true,
      },
    },
  },
  'strapi::favicon',
  'strapi::public',
];
