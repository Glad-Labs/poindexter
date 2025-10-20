module.exports = {
  /**
   * An asynchronous register function that runs before
   * your application is initialized.
   *
   * This gives you an opportunity to set up your data model,
   * run jobs, or perform some special logic.
   */
  register({ strapi }) {
    // ========================================
    // RAILWAY PROXY HTTPS FIX
    // ========================================
    console.log('🔧 Setting up Railway HTTPS proxy middleware...');
    
    // Patch Strapi's server creation to force HTTPS protocol detection
    const originalServer = strapi.server;
    Object.defineProperty(strapi, 'server', {
      get() {
        return originalServer;
      },
      set(value) {
        console.log('✅ Strapi server being set, patching Koa for HTTPS...');
        
        if (value && value.app) {
          const app = value.app;
          
          // Enable proxy trust
          app.proxy = true;
          
          // Intercept all requests to force HTTPS protocol
          app.use(async (ctx, next) => {
            // Force the protocol to be https for Railway proxy
            if (process.env.NODE_ENV === 'production') {
              ctx.request.protocol = 'https';
              ctx.protocol = 'https';
              ctx.secure = true;
              
              // Log once for debugging
              if (!global._railwayHttpsLogged) {
                console.log('✅ Forcing HTTPS protocol for Railway proxy');
                global._railwayHttpsLogged = true;
              }
            }
            await next();
          });
          
          console.log('✅ Railway HTTPS middleware applied');
        }
        
        originalServer = value;
      },
      configurable: true,
    });
  },

  /**
   * An asynchronous bootstrap function that runs before
   * your application gets started.
   *
   * This gives you an opportunity to set up your data model,
   * run jobs, or perform some special logic.
   */
  async bootstrap({ strapi }) {
    // ========================================
    // RAILWAY PROXY COOKIE FIX
    // ========================================
    // Patch Koa to force all cookies to secure: false for Railway proxy
    const app = strapi.server.app;
    
    // Store the original context creation
    const originalCreateContext = app.createContext.bind(app);
    
    // Override createContext to patch cookies.set
    app.createContext = function(req, res) {
      const ctx = originalCreateContext(req, res);
      
      // Store original cookie set method
      const originalSet = ctx.cookies.set.bind(ctx.cookies);
      
      // Override to force secure: false
      ctx.cookies.set = function(name, value, opts = {}) {
        const modifiedOpts = {
          ...opts,
          secure: false, // Railway proxy handles SSL
        };
        return originalSet(name, value, modifiedOpts);
      };
      
      return ctx;
    };
    
    strapi.log.info('✅ Railway proxy cookie patch applied');
    // ========================================

    console.log('🔧 Setting up public permissions for all content types...');

    try {
      // Find the public role
      const publicRole = await strapi.plugins[
        'users-permissions'
      ].services.role.findOne({
        where: { type: 'public' },
      });

      if (!publicRole) {
        console.log('❌ Public role not found');
        return;
      }

      console.log('✓ Found public role');

      const isDev = process.env.NODE_ENV !== 'production';

      // Define the permissions to set for all content types
      const basePermissions = [
        // Single types
        {
          role: publicRole.id,
          type: 'application',
          controller: 'about',
          action: 'find',
          enabled: true,
        },
        {
          role: publicRole.id,
          type: 'application',
          controller: 'privacy-policy',
          action: 'find',
          enabled: true,
        },
        // Collection types - Categories
        {
          role: publicRole.id,
          type: 'application',
          controller: 'category',
          action: 'find',
          enabled: true,
        },
        {
          role: publicRole.id,
          type: 'application',
          controller: 'category',
          action: 'findOne',
          enabled: true,
        },
        // Collection types - Tags
        {
          role: publicRole.id,
          type: 'application',
          controller: 'tag',
          action: 'find',
          enabled: true,
        },
        {
          role: publicRole.id,
          type: 'application',
          controller: 'tag',
          action: 'findOne',
          enabled: true,
        },
        // Collection types - Authors
        {
          role: publicRole.id,
          type: 'application',
          controller: 'author',
          action: 'find',
          enabled: true,
        },
        {
          role: publicRole.id,
          type: 'application',
          controller: 'author',
          action: 'findOne',
          enabled: true,
        },
        // Collection types - Posts
        {
          role: publicRole.id,
          type: 'application',
          controller: 'post',
          action: 'find',
          enabled: true,
        },
        {
          role: publicRole.id,
          type: 'application',
          controller: 'post',
          action: 'findOne',
          enabled: true,
        },
      ];

      // In development only, allow create for seeding convenience
      const devCreatePermissions = isDev
        ? [
            {
              role: publicRole.id,
              type: 'application',
              controller: 'category',
              action: 'create',
              enabled: true,
            },
            {
              role: publicRole.id,
              type: 'application',
              controller: 'tag',
              action: 'create',
              enabled: true,
            },
            {
              role: publicRole.id,
              type: 'application',
              controller: 'author',
              action: 'create',
              enabled: true,
            },
            {
              role: publicRole.id,
              type: 'application',
              controller: 'post',
              action: 'create',
              enabled: true,
            },
          ]
        : [];

      const permissionsToSet = [...basePermissions, ...devCreatePermissions];

      // Set permissions
      for (const permission of permissionsToSet) {
        try {
          // Check if permission already exists
          const existingPermission = await strapi.plugins[
            'users-permissions'
          ].services.permission.findOne({
            where: {
              role: permission.role,
              type: permission.type,
              controller: permission.controller,
              action: permission.action,
            },
          });

          if (existingPermission) {
            // Update existing permission
            await strapi.plugins[
              'users-permissions'
            ].services.permission.update(existingPermission.id, {
              enabled: permission.enabled,
            });
            console.log(
              `✓ Updated permission: ${permission.controller}.${permission.action}`
            );
          } else {
            // Create new permission
            await strapi.plugins[
              'users-permissions'
            ].services.permission.create(permission);
            console.log(
              `✓ Created permission: ${permission.controller}.${permission.action}`
            );
          }
        } catch (error) {
          console.log(
            `❌ Failed to set permission ${permission.controller}.${permission.action}:`,
            error.message
          );
        }
      }

      console.log(
        '🎉 Public permissions setup completed for all content types!'
      );
    } catch (error) {
      console.log('❌ Failed to setup public permissions:', error.message);
    }
  },
};
