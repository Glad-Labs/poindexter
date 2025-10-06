/**
 * @file cms/strapi-backend/src/index.ts
 * @description Main entry point for Strapi server-side custom logic.
 * @overview This file provides two key lifecycle hooks, `register` and `bootstrap`,
 * which allow for extending and customizing the Strapi application. These functions
 * are the primary place to add custom functionalities, integrate with other services,
 * or modify the application's behavior before it starts.
 */

// @ts-ignore - The Strapi type is not being resolved correctly from the installed packages.
// Using `any` for now to avoid blocking compilation. This should be revisited if
// more complex customizations are added that require strong typing of the Strapi instance.
// import type { Strapi } from '@strapi/strapi';

export default {
  /**
   * An asynchronous `register` function that runs before the application is initialized.
   * This function is executed once, when the server is starting up.
   *
   * @param {{ strapi: any }} params - The parameters object, with Strapi instance typed as any.
   *
   * @description This is the ideal place for:
   * - Registering custom fields or plugins.
   * - Adding custom middleware to the chain.
   * - Extending the functionality of core services or content types.
   */
  register({ strapi }: { strapi: any }) {
    // Implementation goes here.
  },

  /**
   * An asynchronous `bootstrap` function that runs after the application has been initialized,
   * but before it starts listening for requests.
   *
   * @param {{ strapi: any }} params - The parameters object, with Strapi instance typed as any.
   *
   * @description This is the ideal place for:
   * - Seeding the database with initial data.
   * - Setting up scheduled tasks (cron jobs).
   * - Initializing connections to third-party services.
   * - Granting default permissions for roles.
   */
  bootstrap({ strapi }: { strapi: any }) {
    // Implementation goes here.
  },
};
