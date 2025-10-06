/**
 * @file cms/strapi-backend/config/database.ts
 * @description Strapi database connection configuration.
 * @overview This file dynamically configures the database connection based on environment
 * variables. It supports multiple database clients (SQLite, PostgreSQL, MySQL) and
 * allows for detailed connection settings, including SSL and connection pooling.
 * The default configuration is set up for a local SQLite database, which is ideal
 * for development and testing.
 */

import path from 'path';

/**
 * @interface StrapiEnv
 * @description Defines the shape of the Strapi `env` utility function, including
 * type-casting helpers.
 */
interface StrapiEnv {
  (key: string, defaultValue?: any): any;
  bool(key: string, defaultValue?: boolean): boolean;
  int(key: string, defaultValue?: number): number;
}

export default ({ env }: { env: StrapiEnv }) => {
  // Determine the database client from environment variables, defaulting to SQLite.
  const client: 'sqlite' | 'mysql' | 'postgres' = env(
    'DATABASE_CLIENT',
    'sqlite'
  );

  // A map of connection configurations for different database clients.
  const connections = {
    mysql: {
      connection: {
        host: env('DATABASE_HOST', 'localhost'),
        port: env.int('DATABASE_PORT', 3306),
        database: env('DATABASE_NAME', 'strapi'),
        user: env('DATABASE_USERNAME', 'strapi'),
        password: env('DATABASE_PASSWORD', 'strapi'),
        // SSL configuration is enabled only if DATABASE_SSL is true.
        ssl: env.bool('DATABASE_SSL', false) && {
          key: env('DATABASE_SSL_KEY', undefined),
          cert: env('DATABASE_SSL_CERT', undefined),
          ca: env('DATABASE_SSL_CA', undefined),
          capath: env('DATABASE_SSL_CAPATH', undefined),
          cipher: env('DATABASE_SSL_CIPHER', undefined),
          rejectUnauthorized: env.bool(
            'DATABASE_SSL_REJECT_UNAUTHORIZED',
            true
          ),
        },
      },
      // Connection pool settings.
      pool: {
        min: env.int('DATABASE_POOL_MIN', 2),
        max: env.int('DATABASE_POOL_MAX', 10),
      },
    },
    postgres: {
      connection: {
        // Use a single connection string if provided (e.g., by Heroku, Render).
        connectionString: env('DATABASE_URL'),
        host: env('DATABASE_HOST', 'localhost'),
        port: env.int('DATABASE_PORT', 5432),
        database: env('DATABASE_NAME', 'strapi'),
        user: env('DATABASE_USERNAME', 'strapi'),
        password: env('DATABASE_PASSWORD', 'strapi'),
        ssl: env.bool('DATABASE_SSL', false) && {
          rejectUnauthorized: env.bool(
            'DATABASE_SSL_REJECT_UNAUTHORIZED',
            true
          ),
        },
        schema: env('DATABASE_SCHEMA', 'public'),
      },
      pool: {
        min: env.int('DATABASE_POOL_MIN', 2),
        max: env.int('DATABASE_POOL_MAX', 10),
      },
    },
    sqlite: {
      connection: {
        // The SQLite file is stored in `.tmp/data.db` by default.
        // This path is relative to the project root.
        filename: path.join(
          __dirname,
          '..',
          '..',
          env('DATABASE_FILENAME', '.tmp/data.db')
        ),
      },
      // Required for SQLite to function correctly.
      useNullAsDefault: true,
    },
  };

  return {
    connection: {
      client,
      // Spread the configuration for the selected client.
      ...connections[client],
      // Set a timeout for acquiring a connection from the pool.
      acquireConnectionTimeout: env.int('DATABASE_CONNECTION_TIMEOUT', 60000),
    },
  };
};
