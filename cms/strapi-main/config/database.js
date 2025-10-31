module.exports = ({ env }) => {
  // Check for full DATABASE_URL first (Railway production pattern)
  const databaseUrl = env('DATABASE_URL');
  if (databaseUrl && databaseUrl.includes('postgresql')) {
    return {
      connection: {
        client: 'postgres',
        connection: {
          connectionString: databaseUrl,
        },
        debug: false,
        pool: { min: 0, max: 7 },
      },
    };
  }

  // Fallback to DATABASE_CLIENT environment variable
  const client = env('DATABASE_CLIENT', 'sqlite');

  if (client === 'postgres') {
    return {
      connection: {
        client: 'postgres',
        connection: {
          connectionString: env('DATABASE_URL'),
        },
        debug: false,
        pool: { min: 0, max: 7 },
      },
    };
  }

  // Default to SQLite for local development
  return {
    connection: {
      client: 'sqlite',
      connection: {
        filename: env('DATABASE_FILENAME', './.tmp/data.db'),
      },
      useNullAsDefault: true,
    },
  };
};
