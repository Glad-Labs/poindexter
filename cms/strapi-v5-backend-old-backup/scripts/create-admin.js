/**
 * Create Admin User with Proper Bcrypt Hash
 * 
 * Run this on Railway: railway run node scripts/create-admin.js
 * 
 * This creates an admin user with a properly hashed password.
 */

async function createAdmin() {
  console.log('🔧 Loading Strapi...\n');
  
  // Import Strapi's crypto utilities
  const crypto = require('crypto');
  
  // Bcrypt hash function (compatible with Strapi)
  const bcrypt = require('bcryptjs');
  
  // Admin credentials
  const email = 'admin@gladlabs.io';
  const password = 'TempPassword123!'; // Change this after first login!
  const firstname = 'Admin';
  const lastname = 'User';

  console.log('👤 Creating admin user:');
  console.log(`   Email: ${email}`);
  console.log(`   Password: ${password}`);
  console.log(`   (Change this after logging in!)\n`);

  // Hash the password
  console.log('🔐 Hashing password...');
  const hashedPassword = await bcrypt.hash(password, 10);
  console.log(`   Hash: ${hashedPassword.substring(0, 20)}...\n`);

  // PostgreSQL connection
  const { Client } = require('pg');
  const client = new Client({
    connectionString: process.env.DATABASE_URL,
  });

  try {
    console.log('🔌 Connecting to PostgreSQL...');
    await client.connect();
    console.log('✅ Connected!\n');

    // Check if admin already exists
    console.log('🔍 Checking for existing admin...');
    const existingAdmin = await client.query(
      'SELECT id, email FROM admin_users WHERE email = $1',
      [email]
    );

    if (existingAdmin.rows.length > 0) {
      console.log('⚠️  Admin already exists! Updating password...\n');
      
      await client.query(
        'UPDATE admin_users SET password = $1, "isActive" = true, blocked = false WHERE email = $2',
        [hashedPassword, email]
      );
      
      console.log('✅ Password updated!\n');
    } else {
      console.log('➕ Creating new admin user...\n');
      
      // Insert new admin
      await client.query(
        `INSERT INTO admin_users (
          email, 
          password, 
          firstname, 
          lastname, 
          "isActive", 
          blocked,
          "preferedLanguage",
          "createdAt",
          "updatedAt"
        ) VALUES ($1, $2, $3, $4, $5, $6, $7, NOW(), NOW())`,
        [email, hashedPassword, firstname, lastname, true, false, 'en']
      );
      
      console.log('✅ Admin user created!\n');

      // Assign super admin role (role ID 1 is typically super admin)
      const adminResult = await client.query(
        'SELECT id FROM admin_users WHERE email = $1',
        [email]
      );
      const adminId = adminResult.rows[0].id;

      // Check if role assignment exists
      const roleCheck = await client.query(
        'SELECT * FROM admin_users_roles_links WHERE user_id = $1 AND role_id = 1',
        [adminId]
      );

      if (roleCheck.rows.length === 0) {
        await client.query(
          'INSERT INTO admin_users_roles_links (user_id, role_id) VALUES ($1, $2)',
          [adminId, 1]
        );
        console.log('✅ Super Admin role assigned!\n');
      }
    }

    // Verify the admin user
    const verifyResult = await client.query(
      'SELECT id, email, firstname, lastname, "isActive", blocked FROM admin_users WHERE email = $1',
      [email]
    );
    
    console.log('📋 Admin User Details:');
    console.table(verifyResult.rows);

    console.log('\n=================================');
    console.log('✅ Admin Setup Complete!');
    console.log('=================================');
    console.log(`Email: ${email}`);
    console.log(`Password: ${password}`);
    console.log('=================================');
    console.log('\n🌐 Login at:');
    console.log('https://glad-labs-strapi-v5-backend-production.up.railway.app/admin');
    console.log('\n⚠️  IMPORTANT: Change this password after logging in!\n');

  } catch (error) {
    console.error('❌ Error:', error.message);
    console.error(error.stack);
    process.exit(1);
  } finally {
    await client.end();
    console.log('🔌 Disconnected from database\n');
  }
}

createAdmin();
