module.exports = {
  /**
   * An asynchronous register function that runs before
   * your application is initialized.
   *
   * This gives you an opportunity to set up your data model,
   * run jobs, or perform some special logic.
   */
  register(/*{ strapi }*/) {},

  /**
   * An asynchronous bootstrap function that runs before
   * your application gets started.
   *
   * This gives you an opportunity to set up your data model,
   * run jobs, or perform some special logic.
   */
  async bootstrap({ strapi }) {
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

      // Define the permissions to set for all content types
      const permissionsToSet = [
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
        {
          role: publicRole.id,
          type: 'application',
          controller: 'category',
          action: 'create',
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
        {
          role: publicRole.id,
          type: 'application',
          controller: 'tag',
          action: 'create',
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
        {
          role: publicRole.id,
          type: 'application',
          controller: 'author',
          action: 'create',
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
        {
          role: publicRole.id,
          type: 'application',
          controller: 'post',
          action: 'create',
          enabled: true,
        },
        // Content Metrics
        {
          role: publicRole.id,
          type: 'application',
          controller: 'content-metric',
          action: 'find',
          enabled: true,
        },
        {
          role: publicRole.id,
          type: 'application',
          controller: 'content-metric',
          action: 'findOne',
          enabled: true,
        },
        {
          role: publicRole.id,
          type: 'application',
          controller: 'content-metric',
          action: 'create',
          enabled: true,
        },
      ];

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
