/**
 * Middleware: force-https
 *
 * This is a custom middleware to forcefully correct the request scheme
 * when running behind a reverse proxy like Railway.
 *
 * Problem:
 * Strapi may not correctly detect the 'https' protocol from the 'X-Forwarded-Proto'
 * header sent by the proxy, leading to "Cannot send secure cookie" errors.
 *
 * Solution:
 * This middleware runs at the very beginning of the request lifecycle. It checks
 * for the 'x-forwarded-proto' header. If it's 'https', it manually sets
 * `ctx.scheme = 'https'`, ensuring all subsequent parts of Strapi
 * (security, sessions, etc.) see the connection as secure.
 */
export default () => {
  return async (ctx, next) => {
    if (ctx.request.header['x-forwarded-proto'] === 'https') {
      ctx.scheme = 'https';
    }
    await next();
  };
};
