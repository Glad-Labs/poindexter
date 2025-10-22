#!/usr/bin/env node
/**
 * Reset Admin Password Script
 * Run this with: railway run node scripts/reset-admin.js
 */

const { default: Strapi } = require('@strapi/strapi');

async function resetAdminPassword() {
  try {
    console.log('Starting Strapi instance...');
    const strapi = await Strapi().load();

    const newPassword = 'TempPassword123!'; // Change this after logging in!
    const adminEmail = 'admin@gladlabs.io';

    console.log(`Looking for admin user: ${adminEmail}`);

    // Find the admin user
    const admins = await strapi.query('admin::user').findMany({
      where: { email: adminEmail },
    });

    if (admins.length === 0) {
      console.error(`❌ No admin user found with email: ${adminEmail}`);
      console.log('\nCreating new admin user...');

      // Create new admin
      const hashedPassword =
        await strapi.admin.services.auth.hashPassword(newPassword);

      await strapi.query('admin::user').create({
        data: {
          email: adminEmail,
          firstname: 'Admin',
          lastname: 'User',
          password: hashedPassword,
          isActive: true,
          roles: [1], // Super Admin role
        },
      });

      console.log('✅ Admin user created successfully!');
    } else {
      console.log('✅ Found admin user, updating password...');

      const admin = admins[0];
      const hashedPassword =
        await strapi.admin.services.auth.hashPassword(newPassword);

      await strapi.query('admin::user').update({
        where: { id: admin.id },
        data: {
          password: hashedPassword,
          isActive: true,
        },
      });

      console.log('✅ Password updated successfully!');
    }

    console.log('\n=================================');
    console.log('✅ Admin Reset Complete!');
    console.log('=================================');
    console.log(`Email: ${adminEmail}`);
    console.log(`Password: ${newPassword}`);
    console.log('=================================');
    console.log('\n⚠️  IMPORTANT: Change this password after logging in!\n');

    await strapi.destroy();
    process.exit(0);
  } catch (error) {
    console.error('❌ Error resetting admin:', error);
    process.exit(1);
  }
}

resetAdminPassword();
