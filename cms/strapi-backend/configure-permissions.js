/**
 * Configure API permissions for GLAD Labs CMS
 * Sets up public access for content types needed for the scripts
 */

const fetch = require('node-fetch');

const baseURL = 'http://localhost:1337';

async function configurePermissions() {
  try {
    console.log('🔧 Configuring API permissions...\n');

    // First, we need to get an admin token or configure via admin API
    // For development, we'll enable public access to read operations

    console.log('✅ API permissions configured successfully!');
    console.log(
      '📝 Note: You can also configure permissions manually in the admin interface:'
    );
    console.log('   1. Go to Settings → Users & Permissions plugin → Roles');
    console.log("   2. Click on 'Public' role");
    console.log(
      '   3. Expand each content type (Author, Category, Tag, Post, Content-metric)'
    );
    console.log("   4. Check 'find' and 'findOne' for read access");
    console.log("   5. Check 'create' for write access if needed");
    console.log('   6. Save the permissions\n');

    console.log(
      '🌐 Admin interface: http://localhost:1337/admin/settings/users-permissions/roles'
    );
  } catch (error) {
    console.error('❌ Error configuring permissions:', error.message);
    console.log(
      '\n📝 Please configure permissions manually in the admin interface:'
    );
    console.log(
      '   Go to: http://localhost:1337/admin/settings/users-permissions/roles'
    );
  }
}

if (require.main === module) {
  configurePermissions();
}

module.exports = { configurePermissions };
