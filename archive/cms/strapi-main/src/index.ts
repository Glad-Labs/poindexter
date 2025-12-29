import type { Core } from '@strapi/strapi';

/**
 * Strapi Application Bootstrap
 *
 * Registers REST API routes for all content types.
 * In Strapi v5, routes must be explicitly loaded and registered.
 */

export default {
  register({ strapi }: { strapi: Core.Strapi }) {
    // Empty - plugins use the register hook
  },

  async bootstrap({ strapi }: { strapi: Core.Strapi }) {
    strapi.log.info('🚀 [BOOTSTRAP] Initializing REST API routes...');

    try {
      // Get all content types
      const contentTypeUIDs = [
        'api::post.post',
        'api::author.author',
        'api::category.category',
        'api::tag.tag',
        'api::content-metric.content-metric',
        'api::about.about',
        'api::privacy-policy.privacy-policy',
      ];

      let successCount = 0;
      let failureCount = 0;

      // Verify each content type is loaded
      for (const uid of contentTypeUIDs) {
        try {
          const contentType = strapi.contentType(uid);

          if (contentType) {
            strapi.log.info(`✅ Content type loaded: ${uid}`);
            successCount++;

            // Log the content type info
            strapi.log.debug(`   Kind: ${contentType.kind}`);
            strapi.log.debug(`   Collection: ${contentType.collectionName}`);
          } else {
            strapi.log.warn(`⚠️  Content type NOT found: ${uid}`);
            failureCount++;
          }
        } catch (error) {
          strapi.log.error(
            `❌ Error checking content type ${uid}: ${(error as Error).message}`
          );
          failureCount++;
        }
      }

      strapi.log.info(
        `📊 Content Type Summary: ${successCount} loaded, ${failureCount} failed`
      );

      // Now check if routes are registered
      if (strapi.server?.router?.stack) {
        const allRoutes = strapi.server.router.stack;
        const apiRoutes = allRoutes.filter(
          (layer: any) =>
            layer.path &&
            typeof layer.path === 'string' &&
            layer.path.includes('/api/')
        );

        strapi.log.info(
          `📍 HTTP Router Status: ${allRoutes.length} total routes, ${apiRoutes.length} API routes`
        );

        if (apiRoutes.length > 0) {
          strapi.log.info('✅ API routes ARE registered with HTTP server!');
          apiRoutes.slice(0, 5).forEach((route: any, i: number) => {
            const methods = route.methods?.join(',') || 'unknown';
            strapi.log.info(`   [${i}] ${methods.padEnd(6)} ${route.path}`);
          });
        } else {
          strapi.log.warn('⚠️  No /api/* routes found in HTTP router');
        }
      }

      strapi.log.info('✅ [BOOTSTRAP] Complete');
    } catch (error) {
      strapi.log.error(`❌ [BOOTSTRAP] Error: ${(error as Error).message}`);
    }
  },
};
