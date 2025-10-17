/**
 * Custom Middleware for Railway Proxy Cookie Fix
 * 
 * Railway terminates SSL at the proxy level, so the connection to Strapi is HTTP.
 * This middleware intercepts cookie operations and forces secure: false to prevent
 * "Cannot send secure cookie over unencrypted connection" errors.
 * 
 * @see https://github.com/koajs/koa/issues/974
 * @see https://docs.railway.app/deploy/deployments#https-and-ssl
 */
module.exports = (config, { strapi }) => {
  return async (ctx, next) => {
    // Store original cookie set method
    const originalSet = ctx.cookies.set.bind(ctx.cookies);
    
    // Override cookies.set to force secure: false
    ctx.cookies.set = function(name, value, opts = {}) {
      // Force secure to false for all cookies
      const modifiedOpts = {
        ...opts,
        secure: false,
      };
      return originalSet(name, value, modifiedOpts);
    };
    
    await next();
  };
};
