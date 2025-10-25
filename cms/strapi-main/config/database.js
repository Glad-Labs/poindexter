module.exports = ({ env }) => {
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
