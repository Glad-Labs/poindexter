module.exports = ({ env }) => {
  const client = env('DATABASE_CLIENT', 'sqlite');
  const databaseUrl = env('DATABASE_URL');

  // Log for debugging (remove in production if needed)
  if (env.bool('STRAPI_LOG_CONFIG', false)) {
    console.log('[DATABASE CONFIG] Client:', client);
    console.log('[DATABASE CONFIG] Has DATABASE_URL:', !!databaseUrl);
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
