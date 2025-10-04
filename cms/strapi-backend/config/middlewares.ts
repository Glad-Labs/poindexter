/**
 * @file cms/strapi-backend/config/middlewares.ts
 * @description Strapi middleware configuration.
 * @overview This file defines the order and selection of global middlewares for the Strapi application.
 * Middlewares are processed for every incoming request and are crucial for security,
 * data parsing, and other cross-cutting concerns. The order of this array is significant.
 */

export default [
  /**
   * @name strapi::logger
   * @description Logs information about each incoming request.
   */
  'strapi::logger',
  /**
   * @name strapi::errors
   * @description Handles errors that occur during request processing, providing a consistent error response format.
   */
  'strapi::errors',
  /**
   * @name strapi::security
   * @description Applies various security headers (e.g., CSP, X-Frame-Options) to responses.
   * @see ./middlewares/security.js for detailed configuration.
   */
  'strapi::security',
  /**
   * @name strapi::cors
   * @description Manages Cross-Origin Resource Sharing (CORS) to control which domains can access the API.
   * @see ./middlewares/cors.js for detailed configuration.
   */
  'strapi::cors',
  /**
   * @name strapi::poweredBy
   * @description Adds the 'X-Powered-By: Strapi' header. Can be disabled for security hardening.
   */
  'strapi::poweredBy',
  /**
   * @name strapi::query
   * @description Parses complex query string parameters for filtering, sorting, and pagination.
   */
  'strapi::query',
  /**
   * @name strapi::body
   * @description Parses the body of incoming requests (e.g., JSON, multipart/form-data).
   */
  'strapi::body',
  /**
   * @name strapi::session
   * @description Manages user sessions, typically for the admin panel.
   */
  'strapi::session',
  /**
   * @name strapi::favicon
   * @description Serves the favicon for the application.
   */
  'strapi::favicon',
  /**
   * @name strapi::public
   * @description Serves static files from the `public` directory.
   */
  'strapi::public',
];
