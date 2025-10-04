/**
 * @file cms/strapi-backend/config/plugins.ts
 * @description Strapi plugin configuration.
 * @overview This file is used to enable, disable, or configure Strapi plugins.
 * By default, it's empty, meaning all installed plugins are enabled with their
 * default settings.
 *
 * @recommendation To configure a specific plugin, you would export an object
 * with a key matching the plugin's name. For example:
 *
 * export default () => ({
 *   'users-permissions': {
 *     config: {
 *       jwtSecret: env('JWT_SECRET'),
 *     },
 *   },
 *   'graphql': {
 *     config: {
 *       endpoint: '/graphql',
 *       shadowCRUD: true,
 *     },
 *   },
 * });
 */

export default () => ({});
