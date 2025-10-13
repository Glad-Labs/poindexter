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
    console.log('🔧 Setting up public permissions for single types...');

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

      // Define the permissions to set
      const permissionsToSet = [
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
          controller: 'about',
          action: 'findOne',
          enabled: true,
        },
        {
          role: publicRole.id,
          type: 'application',
          controller: 'privacy-policy',
          action: 'find',
          enabled: true,
        },
        {
          role: publicRole.id,
          type: 'application',
          controller: 'privacy-policy',
          action: 'findOne',
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

      console.log('🎉 Public permissions setup completed!');
    } catch (error) {
      console.log('❌ Failed to setup public permissions:', error.message);
    }
  },
};
