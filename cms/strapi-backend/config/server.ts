/**
 * @file cms/strapi-backend/config/server.ts
 * @description Strapi server configuration.
 * @overview This file configures the core server settings, such as the host, port,
 * and application keys. These settings are fundamental for running the Strapi
 * application in different environments (development, production).
 */

/**
 * @interface StrapiEnv
 * @description Defines the shape of the Strapi `env` utility function, including
 * type-casting helpers.
 */
interface StrapiEnv {
  (key: string, defaultValue?: any): any;
  int(key: string, defaultValue?: number): number;
  array(key: string, defaultValue?: any[]): any[];
}

export default ({ env }: { env: StrapiEnv }) => ({
  /**
   * @property {string} host - The host the server will listen on.
   * @description '0.0.0.0' makes the server accessible from any network interface,
   * which is standard for containerized or cloud environments.
   * @default '0.0.0.0'
   */
  host: env('HOST', '0.0.0.0'),
  /**
   * @property {number} port - The port the server will listen on.
   * @description The default port for Strapi is 1337. This can be overridden by
   * the `PORT` environment variable.
   * @default 1337
   */
  port: env.int('PORT', 1337),
  /**
   * @property {object} app - Application-specific configurations.
   */
  app: {
    /**
     * @property {string[]} keys - A set of secret keys used for signing cookies and other secure data.
     * @description These keys are critical for security. They should be long, random strings
     * and stored securely in an environment variable, typically as a comma-separated list.
     * @example env.array('APP_KEYS', ['key1', 'key2'])
     */
    keys: env.array('APP_KEYS'),
  },
  /**
   * @property {object} webhooks - Configuration for webhooks.
   * @description This section can be used to configure webhook settings, such as the timeout
   * for webhook requests.
   * @recommendation It's good practice to configure a reasonable timeout to prevent
   * long-running webhooks from blocking server resources.
   */
  webhooks: {
    /**
     * @property {number} timeout - The timeout in milliseconds for webhook requests.
     * @default 3000
     */
    defaultTimeout: 3000,
  },
});
