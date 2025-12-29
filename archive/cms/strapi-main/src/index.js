'use strict';

module.exports = {
  register(/*{ strapi }*/) {},

  async bootstrap({ strapi }) {
    strapi.log.info('🚀 [BOOTSTRAP] Initializing REST API routes...');

    try {
      const contentTypes = [
        'api::post.post',
        'api::author.author',
        'api::category.category',
        'api::tag.tag',
        'api::content-metric.content-metric',
        'api::about.about',
        'api::privacy-policy.privacy-policy',
      ];

      let registeredCount = 0;

      for (const uid of contentTypes) {
        try {
          const contentType = strapi.contentType(uid);
          if (contentType) {
            strapi.log.info(`✅ Content type loaded: ${uid}`);
            registeredCount++;
          }
        } catch (error) {
          strapi.log.error(
            `❌ Error loading content type ${uid}: ${error.message}`
          );
        }
      }

      strapi.log.info(`📊 Content Type Summary: ${registeredCount} loaded`);

      // DETAILED DIAGNOSTIC: Check multiple router sources
      strapi.log.info('🔍 DETAILED ROUTE DIAGNOSTIC:');

      // Method 1: Check strapi.server.router.stack
      if (strapi.server && strapi.server.router && strapi.server.router.stack) {
        strapi.log.info(
          `   ✅ strapi.server.router.stack exists: ${strapi.server.router.stack.length} routes`
        );
        const allRoutes = strapi.server.router.stack;
        allRoutes.slice(0, 5).forEach((layer, i) => {
          const path = layer.path || layer.route?.path || '(unknown)';
          const method =
            layer.method || layer.route?.stack?.[0]?.method || 'ALL';
          strapi.log.info(`      [${i}] ${method.padEnd(6)} ${path}`);
        });
      } else {
        strapi.log.info('   ❌ strapi.server.router.stack: NOT ACCESSIBLE');
      }

      // Method 2: Check if strapi.controller can find controllers
      strapi.log.info('   🔎 Checking if controllers are loaded...');
      for (const uid of contentTypes) {
        try {
          const ctrl = strapi.controller(uid);
          if (ctrl && typeof ctrl === 'object') {
            strapi.log.info(`      ✅ Controller found for ${uid}`);
          } else {
            strapi.log.warn(
              `      ⚠️  Controller not ready or not an object for ${uid}`
            );
          }
        } catch (e) {
          strapi.log.warn(
            `      ❌ Cannot access controller ${uid}: ${e.message}`
          );
        }
      }

      // Method 3: Check REST plugin status
      strapi.log.info('   🔎 Checking REST plugin...');
      if (strapi.plugin('rest')) {
        strapi.log.info('      ✅ REST plugin is active');
      } else {
        strapi.log.warn('      ⚠️  REST plugin not found');
      }

      // NOT attempting manual registration - routes should auto-load from /src/api/*/routes/

      // Configure public permissions for all endpoints
      strapi.log.info('� CONFIGURING PUBLIC PERMISSIONS...');
      try {
        const publicRole = await strapi
          .query('plugin::users-permissions.role')
          .findOne({ where: { type: 'public' } });

        if (publicRole) {
          strapi.log.info(`   ✅ Found public role (ID: ${publicRole.id})`);

          for (const uid of contentTypes) {
            try {
              // Permission format: api::post.post.find (NOT api::post-post.post-post.find)
              const permissionStr = `${uid}.find`;

              const existing = await strapi
                .query('plugin::users-permissions.permission')
                .findOne({
                  where: {
                    role: publicRole.id,
                    action: permissionStr,
                  },
                });

              if (!existing) {
                await strapi
                  .query('plugin::users-permissions.permission')
                  .create({
                    data: {
                      action: permissionStr,
                      role: publicRole.id,
                      enabled: true,
                    },
                  });
                strapi.log.info(
                  `      ✅ Enabled public access: ${permissionStr}`
                );
              } else {
                strapi.log.info(
                  `      ℹ️  Permission already exists for ${permissionStr}`
                );
              }
            } catch (err) {
              strapi.log.warn(
                `      ⚠️  Could not set permission for ${uid}: ${err.message}`
              );
            }
          }
        } else {
          strapi.log.warn('   ⚠️  Public role not found');
        }
      } catch (permErr) {
        strapi.log.warn(`⚠️  Permission config error: ${permErr.message}`);
      }

      strapi.log.info('✅ [BOOTSTRAP] Complete');
    } catch (error) {
      strapi.log.error(`❌ [BOOTSTRAP] Error: ${error.message}`);
      strapi.log.error(error.stack);
    }
  },
};
