/**
 * @file cms/strapi-backend/config/admin.ts
 * @description Strapi admin panel configuration.
 * @overview This file configures the security settings for the Strapi admin panel,
 * including secrets for authentication (JWT), API tokens, and transfer tokens.
 * It is critical for securing the administrative interface of the CMS.
 * All values are sourced from environment variables for security and flexibility.
 */

/**
 * @interface StrapiEnv
 * @description Defines the shape of the Strapi `env` utility function.
 * This utility is used to read environment variables and includes helpers
 * for type casting (e.g., to boolean, integer).
 */
interface StrapiEnv {
  (key: string, defaultValue?: any): any;
  bool(key: string, defaultValue?: boolean): boolean;
  int(key: string, defaultValue?: number): number;
  array(key: string, defaultValue?: any[]): any[];
}

export default ({ env }: { env: StrapiEnv }) => ({
  /**
   * @property {object} auth - Authentication settings for the admin panel.
   */
  auth: {
    /**
     * @property {string} secret - The JWT secret for signing admin user sessions.
     * @description This secret is used to create and verify JSON Web Tokens for admin users.
     * It should be a long, random, and private string stored securely in an environment variable.
     * @example env('ADMIN_JWT_SECRET', 'a-very-secure-random-string')
     */
    secret: env('ADMIN_JWT_SECRET'),
  },
  /**
   * @property {object} apiToken - Settings for Strapi's API tokens (v4).
   */
  apiToken: {
    /**
     * @property {string} salt - A salt used to hash API tokens.
     * @description This adds an additional layer of security for the API tokens stored in the database.
     * It should be a unique, random string.
     * @example env('API_TOKEN_SALT', 'another-secure-random-string')
     */
    salt: env('API_TOKEN_SALT'),
  },
  /**
   * @property {object} transfer - Settings for data transfer features (e.g., push/pull).
   */
  transfer: {
    token: {
      /**
       * @property {string} salt - A salt for hashing transfer tokens.
       * @description Secures the tokens used for transferring data between Strapi instances.
       * @example env('TRANSFER_TOKEN_SALT', 'yet-another-secure-random-string')
       */
      salt: env('TRANSFER_TOKEN_SALT'),
    },
  },
});
