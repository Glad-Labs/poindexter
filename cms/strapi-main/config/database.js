module.exports = ({ env }) => {
  // Check for full DATABASE_URL first (Railway production pattern)
  const databaseUrl = env('DATABASE_URL');
  if (databaseUrl && databaseUrl.includes('postgresql')) {
    return {
      connection: {
        client: 'postgres',
        connection: {
          connectionString: databaseUrl,
          ssl:
            env('NODE_ENV') === 'production'
              ? { rejectUnauthorized: false }
              : false,
        },
        debug: false,
        pool: {
          min: 0,
          max: 5,
          acquireTimeoutMillis: 30000,
          idleTimeoutMillis: 30000,
        },
      },
    };
  }
};
