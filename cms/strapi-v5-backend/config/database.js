module.exports = ({ env }) => {
  const databaseUrl = env('DATABASE_URL');

  // Auto-detect database type from DATABASE_URL if present
  let client = env('DATABASE_CLIENT', '');

  // If DATABASE_CLIENT is not set, auto-detect from DATABASE_URL or default to sqlite
  if (!client || client === '') {
    if (databaseUrl) {
      if (
        databaseUrl.includes('postgres') ||
        databaseUrl.includes('postgresql')
      ) {
        client = 'postgres';
      } else if (databaseUrl.includes('mysql')) {
        client = 'mysql';
      } else {
        client = 'sqlite';
      }
    } else {
      client = 'sqlite';
    }
  }

  // Validate client is a known dialect
  const validDialects = ['sqlite', 'postgres', 'mysql'];
  if (!validDialects.includes(client)) {
    console.error(
      `[DATABASE CONFIG ERROR] Unknown dialect: "${client}". Valid options: ${validDialects.join(', ')}`
    );
    client = 'sqlite'; // Fallback to sqlite
  }

  const connections = {
    sqlite: {
      connection: {
        filename: env('DATABASE_FILENAME', './.tmp/data.db'),
      },
      useNullAsDefault: true,
    },
    postgres: databaseUrl
      ? {
          connection: {
            connectionString: databaseUrl,
            ssl: env.bool('DATABASE_SSL', { rejectUnauthorized: false }),
          },
          pool: { min: 2, max: 10 },
        }
      : {
          connection: {
            host: env('DATABASE_HOST', 'localhost'),
            port: env.int('DATABASE_PORT', 5432),
            database: env('DATABASE_NAME', 'strapi'),
            user: env('DATABASE_USERNAME', 'strapi'),
            password: env('DATABASE_PASSWORD', 'strapi'),
            ssl: env.bool('DATABASE_SSL', false),
            schema: env('DATABASE_SCHEMA', 'public'),
          },
          pool: { min: 2, max: 10 },
        },
    mysql: {
      connection: {
        host: env('DATABASE_HOST', 'localhost'),
        port: env.int('DATABASE_PORT', 3306),
        database: env('DATABASE_NAME', 'strapi'),
        user: env('DATABASE_USERNAME', 'strapi'),
        password: env('DATABASE_PASSWORD', 'strapi'),
        ssl: env.bool('DATABASE_SSL', false),
      },
    },
  };

  return {
    connection: {
      client,
      ...connections[client],
      acquireConnectionTimeout: env.int('DATABASE_CONNECTION_TIMEOUT', 60000),
    },
  };
};
