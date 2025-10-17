/**
 * custom-header-inspector.ts
 *
 * This middleware logs incoming request headers to help diagnose proxy-related issues.
 * It's useful for debugging how services like Railway are forwarding traffic.
 */
export default () => {
  return async (ctx, next) => {
    console.log('--- Inspecting Headers ---');
    console.log('Request Path:', ctx.path);
    console.log('X-Forwarded-Proto:', ctx.get('x-forwarded-proto'));
    console.log('Host:', ctx.get('host'));
    console.log('--- End Headers ---');
    await next();
  };
};
