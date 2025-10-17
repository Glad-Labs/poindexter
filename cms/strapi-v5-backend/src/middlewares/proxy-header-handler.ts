/**
 * Middleware: proxy-header-handler
 *
 * This middleware is essential for running Strapi behind a reverse proxy like Railway.
 *
 * Problem:
 * Railway terminates SSL and forwards traffic to Strapi via HTTP. Strapi, by default,
 * sees an insecure (HTTP) connection and refuses to send secure session cookies,
 * causing login to fail in production.
 *
 * Solution:
 * This middleware inspects the `X-Forwarded-Proto` header, which the proxy sets to "https".
 * If it sees this header, it manually informs the Koa context (`ctx`) that the connection
 * should be treated as secure (`ctx.scheme = 'https'`).
 *
 * By running this first, all subsequent Strapi middlewares (security, session, etc.)
 * will correctly understand that the connection is secure, resolving the cookie error.
 */
export default () => {
  return async (ctx, next) => {
    if (ctx.request.header['x-forwarded-proto'] === 'https') {
      ctx.scheme = 'https';
    }
    await next();
  };
};
