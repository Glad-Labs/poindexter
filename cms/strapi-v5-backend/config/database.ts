/**
 * Strapi Database Configuration
 *
 * Supports multiple database clients (postgres, mysql, sqlite).
 *
 * Railway.app Production:
 * - DATABASE_CLIENT=postgres (MUST be set in Railway env vars)
 * - DATABASE_URL=postgres://user:pass@host:port/db (auto-provided by Railway)
 * - Use DATABASE_PRIVATE_URL to avoid egress fees
 *
 * Local Development:
 * - Defaults to SQLite for quick setup
 *
 * Important: PostgreSQL driver (pg) must be in package.json dependencies
 *
 * @see https://docs.strapi.io/dev-docs/configurations/database
 * @see https://docs.railway.app/databases/postgresql
 */
import path from 'path';

export default ({ env }) => {
  // CRITICAL: Set DATABASE_CLIENT=postgres in Railway env vars
  const client = env('DATABASE_CLIENT', 'sqlite');

  const connections = {
    mysql: {
      connection: {
        host: env('DATABASE_HOST', 'localhost'),
        port: env.int('DATABASE_PORT', 3306),
        database: env('DATABASE_NAME', 'strapi'),
        user: env('DATABASE_USERNAME', 'strapi'),
        password: env('DATABASE_PASSWORD', 'strapi'),
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
      pool: {
        min: env.int('DATABASE_POOL_MIN', 2),
        max: env.int('DATABASE_POOL_MAX', 10),
      },
    },
    postgres: {
      connection: {
        // Railway PRIVATE_URL: avoids egress fees by using internal network
        // Falls back to DATABASE_URL if PRIVATE_URL not available
        connectionString: env('DATABASE_PRIVATE_URL', env('DATABASE_URL')),
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
      pool: {
        min: env.int('DATABASE_POOL_MIN', 2),
        max: env.int('DATABASE_POOL_MAX', 10),
      },
    },
    sqlite: {
      // SQLite used for local development only
      connection: {
        filename: path.join(
          __dirname,
          '..',
          '..',
          env('DATABASE_FILENAME', '.tmp/data.db')
        ),
      },
      useNullAsDefault: true,
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
