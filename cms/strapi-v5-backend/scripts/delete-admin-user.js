/**
 * Delete Admin User from Railway PostgreSQL
 * 
 * Run this with: railway run node scripts/delete-admin-user.js
 * 
 * This removes the existing admin user so you can register a fresh one.
 */

const { Client } = require('pg');

async function deleteAdminUser() {
  const client = new Client({
    connectionString: process.env.DATABASE_URL,
  });

  try {
    console.log('🔌 Connecting to PostgreSQL...');
    await client.connect();
    console.log('✅ Connected!\n');

    // Check if admin user exists
    console.log('🔍 Checking for existing admin users...');
    const checkResult = await client.query(
      'SELECT id, email, firstname, lastname, "isActive", blocked FROM admin_users'
    );

    if (checkResult.rows.length === 0) {
      console.log('✅ No admin users found. You can register a new one!');
      await client.end();
      return;
    }

    console.log('\n📋 Found admin users:');
    console.table(checkResult.rows);

    // Delete all admin users
    console.log('\n🗑️  Deleting admin users...');
    const deleteResult = await client.query('DELETE FROM admin_users');
    console.log(`✅ Deleted ${deleteResult.rowCount} admin user(s)\n`);

    // Verify deletion
    const verifyResult = await client.query('SELECT COUNT(*) FROM admin_users');
    console.log(`✅ Remaining admin users: ${verifyResult.rows[0].count}\n`);

    console.log('=================================');
    console.log('✅ Admin users cleared!');
    console.log('=================================');
    console.log('You can now register a new admin at:');
    console.log('https://glad-labs-strapi-v5-backend-production.up.railway.app/admin');
    console.log('=================================\n');

  } catch (error) {
    console.error('❌ Error:', error.message);
    process.exit(1);
  } finally {
    await client.end();
    console.log('🔌 Disconnected from database');
  }
}

deleteAdminUser();
